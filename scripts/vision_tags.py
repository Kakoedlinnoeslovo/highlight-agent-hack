"""Tag one frame per indexed segment of every imported SF Streets camera.

Captions rarely name car brands or describe who people are, so a W&B-hosted vision
model looks at each segment's middle frame and estimates counts for a fixed audience
taxonomy. Results are cached per video in ``data/vision``. Counts are model estimates
from single frames, not ground truth; downstream ranking says so.

    uv run python scripts/vision_tags.py
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
    image = _frame(source, seconds)
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


def tag_video(service: HighlightService, client: OpenAI, video, workers: int) -> Path:
    HighlightService._verify_source(video)
    destination = service.config.data_dir / "vision" / f"{video.id}.json"
    moments = city._moments(service, video)
    cached = {}
    if destination.exists():
        try:
            manifest = read_json(destination)
        except json.JSONDecodeError:
            manifest = {}
        if (
            isinstance(manifest, dict)
            and manifest.get("model") == VISION_MODEL
            and manifest.get("video_id") == video.id
            and isinstance(manifest.get("frames"), list)
        ):
            cached = {
                frame["segment_id"]: frame
                for frame in manifest["frames"]
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
        ):
            continue
        try:
            results[moment.id] = _validate_observation(
                {**frame["counts"], "notable": frame.get("notable")}
            )
        except ValueError:
            continue
    pending = [moment for moment in moments if moment.id not in results]
    if not pending and destination.exists():
        return destination
    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            tagged = pool.map(
                lambda m: tag_frame(
                    client, video.local_path, (m.start_seconds + m.end_seconds) / 2
                ),
                pending,
            )
            results.update((moment.id, result) for moment, result in zip(pending, tagged))
    frames = [
        {
            "segment_id": m.id,
            "start_seconds": m.start_seconds,
            "end_seconds": m.end_seconds,
            "seconds": (m.start_seconds + m.end_seconds) / 2,
            **results[m.id],
        }
        for m in moments
    ]
    write_json(destination, {"model": VISION_MODEL, "video_id": video.id, "frames": frames})
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    service = HighlightService()
    config = service.config
    client = OpenAI(
        api_key=config.wandb_api_key,
        base_url=config.wandb_base_url,
        project=config.wandb_project or None,
        timeout=90,
        max_retries=0,
    )
    videos = sorted(
        (
            v
            for v in service.list_videos()
            if v.dataset == "sf-streets" and v.index_status == "ready"
        ),
        key=city._camera_name,
    )
    for video in videos:
        started = time.perf_counter()
        path = tag_video(service, client, video, args.workers)
        frames = read_json(path)["frames"]
        failed = sum(frame["counts"] is None for frame in frames)
        print(
            f"{city._camera_name(video)}: {len(frames)} frames, {failed} failed, "
            f"{time.perf_counter() - started:.0f} s",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
