"""Export verified Corner Scout observations and short evidence clips for Vercel.

Run after importing SF Streets footage and running vision_tags.py. Only the public
snapshot and normalized segment clips are exported; credentials and local paths stay local.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import corner_scout as scout

from highlight_agent.service import HighlightService

ROOT = Path(__file__).resolve().parents[1]
ENCODING = "h264-480p-crf28-24fps-v1"


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


def export_site(destination: Path, workers: int = 4) -> dict:
    service = HighlightService()
    corners = scout.load_corners(service)
    if not corners:
        raise ValueError("Import SF Streets footage and run scripts/vision_tags.py first.")
    public = destination / "public"
    media = public / "media"
    media.mkdir(parents=True, exist_ok=True)
    exported, jobs = [], []
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
            frames.append({
                "segment_id": frame["segment_id"], "counts": frame["counts"],
                "notable": frame.get("notable", ""), "start": start, "end": end,
                "source": scout.city._source_ref(video, start, end),
                "clip": f"/media/{filename}",
            })
        exported.append({
            "id": video.id, "camera": corner["camera"], "headline": corner["headline"],
            "recorded": corner["recorded"], "duration": video.duration, "frames": frames,
        })
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda job: export_clip(*job), jobs))
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
    # Publish only after every clip exists, and remove old clips produced by this exporter.
    temporary = public / "corners.tmp.json"
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(public / "corners.json")
    referenced = {job[3].name for job in jobs}
    for path in media.iterdir():
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{20}\.mp4", path.name):
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
    print(f"Exported {len(result['corners'])} corners and {frames} evidence clips.")


if __name__ == "__main__":
    main()
