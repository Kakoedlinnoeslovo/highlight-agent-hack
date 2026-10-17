"""Tag every indexed segment of reviewed fixed street-camera recordings.

Captions rarely name car brands or describe who people are, so a W&B-hosted vision
model looks at each segment's middle frame and estimates counts for a fixed audience
taxonomy. Results are cached per video in ``data/vision``. Counts are model estimates
from single frames, not ground truth; downstream ranking says so.

    uv run python scripts/vision_tags.py
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import city_report as city
from openai import OpenAI

from highlight_agent.service import HighlightService
from highlight_agent.storage import read_json, write_json

VISION_MODEL = "google/gemma-4-31B-it"

# label, guidance shown to the vision model
TAXONOMY = {
    "office_worker": ("Office workers", "business or smart office clothing"),
    "tourist": ("Tourists", "cameras, maps, sightseeing, luggage"),
    "young_adult": ("Students & young adults", "teens or young adults, school bags"),
    "family_with_children": ("Families with children", "adults with children or strollers"),
    "elderly": ("Older adults", "clearly older people, canes, walkers"),
    "worker_in_uniform": ("Workers in uniform", "hi-vis vests, uniforms, couriers on foot"),
    "casual_adult": ("Other adults", "adults not matching the groups above"),
    "premium_car": ("Premium cars", "luxury or high-end models you can recognise"),
    "mid_range_car": ("Mid-range cars", "ordinary mainstream cars"),
    "budget_car": ("Budget or older cars", "older, worn, or economy cars"),
    "taxi_or_rideshare": ("Taxis & rideshare", "taxis, marked rideshare or robotaxis"),
    "bus": ("Buses", "city buses and coaches"),
    "delivery_vehicle": ("Delivery trucks & vans", "box trucks, delivery vans"),
    "motorcycle_or_scooter": ("Motorcycles & scooters", "motorcycles, mopeds, kick scooters"),
    "cyclist": ("Cyclists", "people riding bicycles; ignore parked bicycles"),
    "dog": ("Dogs", "dogs on the street or sidewalk"),
}

PROMPT = (
    "You count what is visible in one street-camera frame for a small-business location "
    "study. Return only a JSON object with an integer count for exactly these keys, then a "
    '"notable" string of at most twelve words naming the most distinctive thing visible. '
    "Count only what you can actually see; use 0 when unsure and never guess brands you "
    "cannot recognise. Each person or vehicle counts once, in its best-fitting key.\n"
    + "\n".join(f"- {key}: {hint}" for key, (_, hint) in TAXONOMY.items())
)
PROMPT_VERSION = hashlib.sha256(PROMPT.encode()).hexdigest()


def _frame(source: Path, seconds: float) -> str:
    data = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale=1024:-1",
            "-f",
            "image2",
            "-c:v",
            "mjpeg",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def _parse(content: str) -> dict:
    match = re.search(r"\{.*\}", content or "", re.S)
    raw = json.loads(match[0]) if match else None
    return _validate_observation(raw)


def _validate_observation(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != set(TAXONOMY) | {"notable"}:
        raise ValueError("Vision output must include every audience count and a notable string.")
    if not isinstance(raw["notable"], str):
        raise ValueError("Vision output notable must be a string.")
    counts = {}
    for key in TAXONOMY:
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Vision output {key} must be a nonnegative integer.")
        counts[key] = min(50, value)
    notable = raw["notable"][:120]
    return {"counts": counts, "notable": notable}


def tag_frame(client: OpenAI, source: Path, seconds: float) -> dict:
    try:
        image = _frame(source, seconds)
    except Exception as error:
        return {"counts": None, "notable": "", "error": type(error).__name__}
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=VISION_MODEL,
                temperature=0.1,
                max_tokens=400,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {"type": "image_url", "image_url": {"url": image}},
                        ],
                    }
                ],
            )
            return _parse(response.choices[0].message.content)
        except Exception as error:  # one retry, then record the gap honestly
            if attempt:
                return {"counts": None, "notable": "", "error": type(error).__name__}
            time.sleep(2)
    raise AssertionError("unreachable")


def _fingerprint(video, moment) -> str:
    return hashlib.sha256(json.dumps({
        "source_sha256": video.content_sha256, "model": VISION_MODEL,
        "prompt_version": PROMPT_VERSION, "segment_id": moment.id,
        "start": moment.start_seconds, "end": moment.end_seconds,
    }, sort_keys=True).encode()).hexdigest()


def valid_cached_frames(video, moments, manifest: object) -> dict[str, dict]:
    """Only known successful observations of these exact source bytes are evidence."""
    if not (
        isinstance(manifest, dict) and manifest.get("model") == VISION_MODEL
        and manifest.get("video_id") == video.id and isinstance(manifest.get("frames"), list)
    ):
        return {}
    # Existing demo caches predate fingerprints. Full imports always require them.
    legacy = video.dataset == "sf-streets" and "source_sha256" not in manifest
    if not legacy and (
        manifest.get("source_sha256") != video.content_sha256
        or manifest.get("prompt_version") != PROMPT_VERSION
    ):
        return {}
    cached = {
        frame["segment_id"]: frame for frame in manifest["frames"]
        if isinstance(frame, dict) and isinstance(frame.get("segment_id"), str)
    }
    results = {}
    for moment in moments:
        frame = cached.get(moment.id, {})
        if (
            not isinstance(frame.get("counts"), dict)
            or frame.get("start_seconds") != moment.start_seconds
            or frame.get("end_seconds") != moment.end_seconds
            or frame.get("seconds") != (moment.start_seconds + moment.end_seconds) / 2
            or (not legacy and frame.get("fingerprint") != _fingerprint(video, moment))
        ):
            continue
        try:
            _validate_observation(
                {**frame["counts"], "notable": frame.get("notable")}
            )
        except ValueError:
            continue
        results[moment.id] = frame
    return results


def tag_video(service: HighlightService, client: OpenAI, video, workers: int) -> Path:
    HighlightService._verify_source(video)
    if not city.scouting_eligible(service, video):
        raise ValueError("Recording needs a fixed-camera review before audience analysis.")
    destination = service.config.data_dir / "vision" / f"{video.id}.json"
    moments = city._moments(service, video)
    try:
        manifest = read_json(destination)
    except (OSError, ValueError):
        manifest = {}
    results = valid_cached_frames(video, moments, manifest)
    pending = [moment for moment in moments if moment.id not in results]
    if not pending and destination.exists():
        return destination
    for moment in moments:
        if moment.id in results:
            results[moment.id] = {**results[moment.id], "fingerprint": _fingerprint(video, moment)}

    def save():
        frames = [results.get(m.id, {
            "segment_id": m.id, "start_seconds": m.start_seconds, "end_seconds": m.end_seconds,
            "seconds": (m.start_seconds + m.end_seconds) / 2,
            "fingerprint": _fingerprint(video, m), "counts": None, "notable": "",
            "error": "Pending",
        }) for m in moments]
        write_json(destination, {
            "model": VISION_MODEL, "video_id": video.id, "source_sha256": video.content_sha256,
            "prompt_version": PROMPT_VERSION, "frames": frames,
        })

    save()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(tag_frame, client, video.local_path,
                        (moment.start_seconds + moment.end_seconds) / 2): moment
            for moment in pending
        }
        for future in as_completed(futures):
            moment = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {"counts": None, "notable": "", "error": type(error).__name__}
            results[moment.id] = {
                "segment_id": moment.id, "start_seconds": moment.start_seconds,
                "end_seconds": moment.end_seconds,
                "seconds": (moment.start_seconds + moment.end_seconds) / 2,
                "fingerprint": _fingerprint(video, moment), **result,
            }
            save()  # Atomic per completion: an interruption loses at most an in-flight call.
    return destination


def review_samples(service: HighlightService, videos: list) -> list[dict]:
    """Extract first/middle/last stills for a human motion review; never auto-approve."""
    destination = service.config.data_dir / "catalog" / "review-frames"
    destination.mkdir(parents=True, exist_ok=True)
    samples = []
    for video in videos:
        HighlightService._verify_source(video)
        stem = getattr(video, "stream_id", None) or video.id
        seconds = [min(0.5, video.duration / 4), video.duration / 2, max(0, video.duration - 0.5)]
        images = []
        for label, point in zip(("first", "middle", "last"), seconds):
            path = destination / f"{stem}-{video.content_sha256[:12]}-{label}.jpg"
            if not path.exists():
                city._thumbnail(video.local_path, point, path)
            images.append({"seconds": point, "path": str(path)})
        samples.append({
            "stream_id": stem, "video_id": video.id, "camera": city._camera_name(video),
            "city": city._city_name(video), "source_sha256": video.content_sha256,
            "eligible": city.scouting_eligible(service, video), "samples": images,
        })
    write_json(destination / "manifest.json", samples)
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--city", help="Analyze only this city (default: all reviewed cities)")
    parser.add_argument("--video-id", action="append", help="Analyze/review a specific recording")
    parser.add_argument("--review-samples", action="store_true",
                        help="Extract first/middle/last stills without inference or approval")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    service = HighlightService()
    candidates = [v for v in city.preferred_recordings(service)
                  if v.index_status == "ready" and city.scouting_candidate(v)
                  and (not args.city or city._city_name(v) == city.city_label(args.city))
                  and (not args.video_id or v.id in args.video_id)]
    if args.review_samples:
        print(json.dumps(review_samples(service, candidates), indent=2))
        return 0
    config = service.config
    client = OpenAI(
        api_key=config.wandb_api_key,
        base_url=config.wandb_base_url,
        project=config.wandb_project or None,
        timeout=90,
        max_retries=0,
    )
    videos = [v for v in candidates if city.scouting_eligible(service, v)]
    skipped = len(candidates) - len(videos)
    if skipped:
        print(f"{skipped} candidate recordings await fixed-camera review.", file=sys.stderr)
    failed_videos = []
    for video in videos:
        started = time.perf_counter()
        try:
            path = tag_video(service, client, video, args.workers)
        except Exception as error:
            failed_videos.append(video.id)
            print(f"{video.id}: {type(error).__name__}: {error}", file=sys.stderr)
            continue
        frames = read_json(path)["frames"]
        failed = sum(frame["counts"] is None for frame in frames)
        print(
            f"{city._camera_name(video)}: {len(frames)} frames, {failed} failed, "
            f"{time.perf_counter() - started:.0f} s",
            file=sys.stderr,
        )
    return int(bool(failed_videos))


if __name__ == "__main__":
    raise SystemExit(main())
