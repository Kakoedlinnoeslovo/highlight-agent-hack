"""Export verified Corner Scout observations and short evidence clips for Vercel.

Run after importing SF Streets footage and running vision_tags.py. Only the public
snapshot and normalized segment clips are exported; credentials and local paths stay local.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import corner_scout as scout
from vision_tags import _frame as sample_frame

from highlight_agent.models import VideoRecord
from highlight_agent.service import HighlightService

ROOT = Path(__file__).resolve().parents[1]
ENCODING = "h264-480p-crf28-24fps-v1"
IMAGE_ENCODING = "mjpeg-1024w-v1"
# Inspected presentation crop, bound to the exact source and sampled frame.
# These coordinates are not model detections; the uncropped image is always published.
REVIEWED_FOCUS = {
    (
        "5c9f00875b47c3029b8ae7aa6386564765025839eb1a03d4e3ad1f55d916afcf",
        "moment-56b35c1ebeee67742d7e",
        7.5,
    ): {"dog": {"x": 0.125, "y": 0.5, "width": 0.5, "height": 0.5}},
}



def export_clip(source: Path, start: float, end: float, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size:
        return
    temporary = destination.with_suffix(".tmp.mp4")
    try:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(start), "-i", str(source), "-t", str(end - start),
                "-an", "-vf", "scale=-2:480", "-r", "24", "-c:v", "libx264",
                "-preset", "fast", "-crf", "28", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", "-threads", "1", str(temporary),
            ],
            check=True, capture_output=True, timeout=90,
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def export_image(source: Path, seconds: float, destination: Path) -> None:
    """Reconstruct the sampled image from the original using the vision extractor."""
    if destination.is_file() and destination.stat().st_size:
        return
    temporary = destination.with_suffix(".tmp.jpg")
    try:
        uri = sample_frame(source, seconds)
        prefix = "data:image/jpeg;base64,"
        if not uri.startswith(prefix):
            raise ValueError("Sample extraction did not produce a JPEG image.")
        image = base64.b64decode(uri[len(prefix):], validate=True)
        if not image:
            raise ValueError("Sample extraction produced an empty image.")
        temporary.write_bytes(image)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_poster(video: VideoRecord, uri: str, public: Path) -> str:
    """Publish an optional still already verified by ``corner_previews``."""
    prefix = "data:image/jpeg;base64,"
    if not isinstance(uri, str) or not uri.startswith(prefix):
        return ""
    temporary = None
    try:
        image = base64.b64decode(uri[len(prefix):], validate=True)
        if not image:
            return ""
        fingerprint = video.content_sha256[:12] or str(video.local_path.stat().st_mtime_ns)
        destination = public / "previews" / f"{video.id}-{fingerprint}.jpg"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp.jpg")
        temporary.write_bytes(image)
        temporary.replace(destination)
        return f"/previews/{destination.name}"
    except (OSError, ValueError):
        return ""
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def export_site(destination: Path, workers: int = 4) -> dict:
    service = HighlightService()
    corners = scout.load_corners(service)
    if not corners:
        raise ValueError("Import SF Streets footage and run scripts/vision_tags.py first.")
    public = destination / "public"
    media = public / "media"
    media.mkdir(parents=True, exist_ok=True)
    exported, jobs, image_jobs = [], [], []
    for corner in corners:
        video = corner["video"]
        # Verify the same source bytes used for cached observations before publishing evidence.
        HighlightService._verify_source(video)
        frames = []
        for frame in corner["frames"]:
            moment = corner["moments"].get(frame["segment_id"])
            if moment is None:
                raise ValueError(f"Missing source segment for {video.id}.")
            start, end = moment.start_seconds, min(moment.end_seconds, video.duration)
            if not 0 <= start < end <= video.duration:
                raise ValueError(f"Invalid evidence interval for {video.id}.")
            expected = {
                "start_seconds": moment.start_seconds, "end_seconds": moment.end_seconds,
                "seconds": (moment.start_seconds + moment.end_seconds) / 2,
            }
            if any(
                isinstance(frame.get(key), bool)
                or not isinstance(frame.get(key), (int, float))
                or not math.isclose(frame[key], value, rel_tol=0, abs_tol=0.001)
                for key, value in expected.items()
            ):
                raise ValueError(f"Vision observations changed for {video.id}; rerun vision_tags.py.")
            digest = hashlib.sha256(
                f"{video.content_sha256}:{start}:{end}:{ENCODING}".encode()
            ).hexdigest()[:20]
            filename = f"{video.id[:8]}-{digest}.mp4"
            jobs.append((video.local_path, start, end, media / filename))
            sampled_at = frame["seconds"]
            if not start <= sampled_at < end:
                raise ValueError(f"Sampled frame is outside the evidence interval for {video.id}.")
            image_digest = hashlib.sha256(
                f"{video.content_sha256}:{sampled_at:.3f}:{IMAGE_ENCODING}".encode()
            ).hexdigest()[:20]
            image_filename = f"{video.id[:8]}-{image_digest}.jpg"
            image_jobs.append((video.local_path, sampled_at, media / image_filename))
            published_frame = {
                "segment_id": frame["segment_id"], "counts": frame["counts"],
                "notable": frame.get("notable", ""), "start": start, "end": end,
                "source": scout.city._source_ref(video, start, end),
                "clip": f"/media/{filename}", "sampledAt": sampled_at,
                "image": f"/media/{image_filename}",
            }
            focus = REVIEWED_FOCUS.get((
                video.content_sha256, frame["segment_id"], sampled_at,
            ))
            if focus:
                published_frame["focus"] = focus
            frames.append(published_frame)
        exported.append({
            "id": video.id, "camera": corner["camera"], "headline": corner["headline"],
            "recorded": corner["recorded"], "duration": video.duration, "frames": frames,
        })
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda job: export_clip(*job), jobs))
        list(pool.map(lambda job: export_image(*job), image_jobs))
    previews = scout.corner_previews(service, corners)
    for index, item in enumerate(exported):
        uri = previews[index].get("poster", "") if index < len(previews) else ""
        item["poster"] = _publish_poster(corners[index]["video"], uri, public)
    payload = {
        "exportedAt": datetime.now(UTC).isoformat(),
        "audiences": {key: label for key, (label, _) in scout.AUDIENCES.items()},
        "coreThreshold": scout.CORE, "minFrames": scout.MIN_FRAMES, "cap": scout.CAP,
        "examples": scout.EXAMPLES, "corners": exported,
    }
    library = destination / "lib"
    library.mkdir(parents=True, exist_ok=True)
    (library / "profile-config.json").write_text(json.dumps({
        "audiences": payload["audiences"], "named": scout.NAMED,
        "coreThreshold": scout.CORE, "system": scout.PROFILE_SYSTEM,
        "schema": scout.BusinessProfile.model_json_schema(),
    }, indent=2) + "\n")
    (public / "scout_motion.css").write_bytes((ROOT / "scripts" / "scout_motion.css").read_bytes())
    # Publish only after every clip and sampled image exists. A failed extraction leaves
    # the previous snapshot and all of its referenced media in place.
    temporary = public / "corners.tmp.json"
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(public / "corners.json")
    referenced = {job[3].name for job in jobs} | {job[2].name for job in image_jobs}
    for path in media.iterdir():
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{20}\.(mp4|jpg)", path.name):
            if path.name not in referenced:
                path.unlink()
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "web")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    result = export_site(args.output_dir.resolve(), args.workers)
    frames = sum(len(corner["frames"]) for corner in result["corners"])
    print(f"Exported {len(result['corners'])} corners, {frames} clips and {frames} sampled images.")


if __name__ == "__main__":
    main()
