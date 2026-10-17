"""Export the complete supplied collection, with optional resumable Vercel Blob publishing.

    uv run python scripts/export_collection.py
    uv run python scripts/export_collection.py --upload

Only validated full recordings receive playable media. Unimported sources remain
searchable with explicit incomplete coverage; they never masquerade as playable footage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

from highlight_agent.media import probe_media
from highlight_agent.service import HighlightService
from highlight_agent.storage import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
ENCODING = "collection-h264-480p-24fps-250k-aac48k-pts-v2"
MAX_EXPORT_BYTES = 800_000_000
BLOB_STORAGE_BYTES = 1_000_000_000


def media_key(video) -> str:
    return hashlib.sha256(f"{video.content_sha256}:{ENCODING}".encode()).hexdigest()


def encode_recording(video, destination: Path) -> dict:
    """Produce a bounded-bitrate preview while retaining the normalized source locally."""
    HighlightService._verify_source(video)
    metadata = destination.with_suffix(".json")
    if destination.is_file() and metadata.is_file():
        cached = read_json(metadata)
        if (cached.get("source_sha256") == video.content_sha256
                and cached.get("encoding") == ENCODING
                and cached.get("size") == destination.stat().st_size
                and cached.get("sha256") == _hash(destination)):
            return cached
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.mp4")
    try:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video.local_path), "-map", "0:v:0", "-map", "0:a?",
            "-vf", "scale=-2:480,fps=24,setpts=PTS-STARTPTS", "-c:v", "libx264",
            "-preset", "fast", "-b:v", "250k", "-maxrate", "300k", "-bufsize", "500k",
            "-pix_fmt", "yuv420p", "-af", "asetpts=PTS-STARTPTS", "-c:a", "aac", "-b:a", "48k",
            "-t", str(video.duration), "-avoid_negative_ts", "disabled",
            "-movflags", "+faststart", "-threads", "2", str(temporary),
        ], check=True, capture_output=True, timeout=max(180, video.duration * 2))
        measured = probe_media(temporary)
        if abs(measured.duration - video.duration) > 0.1:
            raise ValueError(f"Export duration differs from indexed source for {video.id}.")
        payload = {
            "source_sha256": video.content_sha256, "encoding": ENCODING,
            "duration": measured.duration, "size": temporary.stat().st_size,
            "sha256": _hash(temporary),
        }
        temporary.replace(destination)
        write_json(metadata, payload)
        return payload
    finally:
        temporary.unlink(missing_ok=True)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BlobPublisher:
    """Use the official SDK in a local helper; check quota once before any upload."""
    def __init__(self, web: Path, budget: int = BLOB_STORAGE_BYTES):
        self.web, self.budget = web, budget
        self.env = {**os.environ, **{
            key: value for key, value in dotenv_values(web / ".env.local").items() if value
        }}

    def _call(self, job: dict) -> dict:
        response = subprocess.run(
            ["node", str(ROOT / "web/scripts/blob-media.mjs")],
            input=json.dumps(job), text=True, capture_output=True, env=self.env,
            timeout=600, check=False,
        )
        if response.returncode:
            raise RuntimeError("Blob publishing failed. Check local credentials and storage quota.")
        return json.loads(response.stdout)

    def publish(self, files: dict[str, Path], progress=print) -> dict[str, str]:
        blobs = self._call({"action": "list"})["blobs"]
        existing = {item["pathname"]: item for item in blobs}
        names = {key: f"{'frames' if path.suffix == '.jpg' else 'recordings'}/{key}{path.suffix}"
                 for key, path in files.items()}
        pending = {key: path for key, path in files.items()
                   if names[key] not in existing}
        if any(existing[names[key]]["size"] != path.stat().st_size
               for key, path in files.items() if names[key] in existing):
            raise ValueError("An existing content-addressed asset has a different size.")
        used = sum(item["size"] for item in blobs)
        needed = sum(path.stat().st_size for path in pending.values())
        if used + needed > self.budget:
            raise ValueError("Blob quota preflight failed; no new recordings were uploaded.")
        def send(job):
            number, (key, path) = job
            progress(f"Upload {number}/{len(pending)}: {path.name}")
            pathname = names[key]
            result = self._call({
                "action": "upload", "path": str(path), "pathname": pathname,
            })
            return pathname, result
        with ThreadPoolExecutor(max_workers=4) as pool:
            existing.update(pool.map(send, enumerate(pending.items(), 1)))
        return {key: existing[names[key]]["url"] for key in files}


def _inventory(service: HighlightService) -> dict:
    saved = service.config.data_dir / "catalog/inventory.json"
    if saved.is_file():
        inventory = read_json(saved)
        if isinstance(inventory, dict) and isinstance(inventory.get("chunks"), list):
            if inventory.get("missing_sources"):
                baseline = ROOT / "outputs/validation/public-dataset-inventory.json"
                if not baseline.is_file():
                    raise ValueError("Saved sources are missing and their caption inventory is unavailable.")
                missing = set(inventory["missing_sources"])
                inventory["chunks"] += [row for row in read_json(baseline)["chunks"]
                                         if row["original_video"] in missing]
            return inventory
    baseline = ROOT / "outputs/validation/public-dataset-inventory.json"
    if baseline.is_file():
        return read_json(baseline)
    raise ValueError("Discover/import the supplied collection before exporting it.")


def _totals(recordings: list[dict]) -> dict:
    return {
        "recordings": len(recordings), "chunks": sum(item["chunk_count"] for item in recordings),
        "segments": sum(len(item["segments"]) for item in recordings),
        "seconds": sum(item["duration"] for item in recordings),
    }


def _recording_groups(inventory: dict) -> list[tuple[str, list[dict]]]:
    streams = defaultdict(list)
    for chunk in inventory["chunks"]:
        if chunk.get("is_public") is True:
            streams[chunk["stream_id"]].append(chunk)
    result = []
    for stream_id, chunks in streams.items():
        chunks.sort(key=lambda row: row["chunk_index"])
        run = []
        for chunk in chunks:
            if run and chunk["chunk_index"] != run[-1]["chunk_index"] + 1:
                result.append((stream_id, run))
                run = []
            run.append(chunk)
        if run:
            result.append((stream_id, run))
    return result


def _indexed_rows(service, video, chunks: list[dict]) -> list[dict]:
    rows = service.indexed_segments(video.id)
    expected = []
    for source, raw in zip(video.source_chunks, chunks, strict=True):
        for segment in raw.get("timeline", []):
            expected.append((source.start_seconds + segment["segment_start_sec"],
                             source.start_seconds + segment["segment_end_sec"]))
    if len(rows) != len(expected) or len({row["id"] for row in rows}) != len(expected):
        raise ValueError("Incomplete or duplicate local caption coverage.")
    rows = sorted(rows, key=lambda row: row["start_seconds"])
    for row, (left, right) in zip(rows, expected, strict=True):
        start, end = row["start_seconds"], row["end_seconds"]
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not 0 <= start < end <= video.duration + 0.01
                or not math.isclose(start, left, abs_tol=0.05, rel_tol=0)
                or not math.isclose(end, right, abs_tol=0.05, rel_tol=0)):
            raise ValueError("A local caption interval differs from its supplied source.")
    if HighlightService._with_index_status(video, rows).index_status != "ready":
        raise ValueError("Incomplete local caption timeline.")
    return rows


def _matches_recording(video, stream_id: str, chunks: list[dict]) -> bool:
    if [chunk.original_video for chunk in video.source_chunks] != [chunk["original_video"] for chunk in chunks]:
        return False
    first = chunks[0]
    source = {
        "stream_id": stream_id,
        **{key: first[key] for key in ("camera_id", "location", "capture_type")},
        "chunks": [{"original_video": chunk["original_video"],
                    "duration": float(chunk["chunk_duration_sec"]),
                    "chunk_index": chunk["chunk_index"],
                    "segment_count": chunk["total_segments"]} for chunk in chunks],
    }
    if video.import_signature:
        return video.import_signature == HighlightService.dataset_import_signature(source)
    # Earlier records may lack a signature; retain only matching known metadata.
    return all(getattr(video, key) in (None, source[key])
               for key in ("camera_id", "location", "capture_type"))


def build_catalog(service: HighlightService, urls: dict[str, str], local=False) -> dict:
    inventory = _inventory(service)
    grouped = _recording_groups(inventory)
    # Only full immutable records can satisfy a discovered stream's membership.
    imported = defaultdict(list)
    for video in service.list_videos():
        if video.dataset == "hackathon-public" and video.index_status == "ready":
            imported[video.stream_id].append(video)
    recordings, failures = [], []
    for stream_id, chunks in grouped:
        video = next((item for item in imported[stream_id]
                      if _matches_recording(item, stream_id, chunks)), None)
        first, segments, offset = chunks[0], [], 0.0
        for chunk in chunks:
            for segment in chunk.get("timeline", []):
                start, end = segment.get("segment_start_sec"), segment.get("segment_end_sec")
                if (isinstance(start, bool) or isinstance(end, bool)
                        or not isinstance(start, (int, float))
                        or not isinstance(end, (int, float))
                        or not math.isfinite(start) or not math.isfinite(end)
                        or not 0 <= start < end <= chunk["chunk_duration_sec"] + 0.01):
                    raise ValueError("Invalid supplied caption interval in catalog inventory.")
                segments.append({
                    "id": hashlib.sha256(
                        f"{chunk['original_video']}:{start}:{end}".encode()
                    ).hexdigest()[:24],
                    "start": offset + start, "end": offset + end,
                    "caption": segment.get("reasoning_content", ""),
                    "source": f"{chunk['original_video']} @ {start:g}–{end:g} s",
                    "object_classes": segment.get("object_classes", ""),
                    "object_counts": segment.get("object_counts", ""),
                })
            offset += chunk["chunk_duration_sec"]
        key, media = None, None
        if video:
            try:
                HighlightService._verify_source(video)
                # Use measured local intervals rather than accumulating nominal lengths.
                rows = _indexed_rows(service, video, chunks)
                segments = [{
                    "id": row["id"], "start": row["start_seconds"], "end": row["end_seconds"],
                    "caption": row["caption"],
                    "source": _source_reference(video, row["start_seconds"], row["end_seconds"]),
                    "object_classes": segments[index].get("object_classes", ""),
                    "object_counts": segments[index].get("object_counts", ""),
                } for index, row in enumerate(rows)]
                key = media_key(video)
                if key in urls:
                    media = {"url": f"/recordings/{key}.mp4" if local else urls[key],
                             "local_url": f"/recordings/{key}.mp4", "duration": video.duration}
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                failures.append({"stream_id": stream_id, "error": type(error).__name__})
                video = None
        recordings.append({
            "id": video.id if video else f"{stream_id}-{chunks[0]['chunk_index']}",
            "stream_id": stream_id,
            "camera_id": first["camera_id"], "location": first["location"],
            "capture_type": first["capture_type"],
            "label": f"{first['location'].replace('_', ' ').title()} · "
                     f"{first['camera_id']} · {stream_id[:8]}",
            "duration": video.duration if video else offset, "chunk_count": len(chunks),
            "imported": video is not None, "media": media, "segments": segments,
        })
    recordings.sort(key=lambda item: (item["location"], item["camera_id"], item["stream_id"]))
    expected = _totals(recordings)
    expected["seconds"] = sum(chunk["chunk_duration_sec"] for _, chunks in grouped for chunk in chunks)
    present = _totals([item for item in recordings if item["imported"]])
    playable = _totals([item for item in recordings if item["media"]])
    declared_segments = sum(chunk["total_segments"] for _, chunks in grouped for chunk in chunks)
    # Inventory caption timelines must be complete even before import.
    if expected["segments"] != declared_segments:
        raise ValueError("The inventory does not include every declared caption segment.")
    return {
        "version": 1, "exportedAt": datetime.now(UTC).isoformat(),
        "coverage": {"expected": expected, "imported": present, "playable": playable,
                     "complete": expected["chunks"] > 0
                     and playable["chunks"] == expected["chunks"]
                     and playable["segments"] == declared_segments and not failures,
                     "failures": failures}, "recordings": recordings,
    }


def _source_reference(video, start: float, end: float) -> str:
    references = []
    for chunk in video.source_chunks:
        left, right = max(start, chunk.start_seconds), min(end, chunk.start_seconds + chunk.duration)
        if right > left:
            references.append(f"{chunk.original_video} @ {left-chunk.start_seconds:g}–"
                              f"{right-chunk.start_seconds:g} s")
    return "; ".join(references)


def export_collection(service: HighlightService, web: Path, upload=False, workers=2,
                      publisher=None, progress=print) -> dict:
    selected = build_catalog(service, {})
    videos = [service.get_video(item["id"]) for item in selected["recordings"] if item["imported"]]
    unique_videos = {media_key(video): video for video in videos}
    media = service.config.output_dir / "collection/media"
    files = {key: media / f"{key}.mp4" for key in unique_videos}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda key: encode_recording(unique_videos[key], files[key]), unique_videos))
    size = sum(path.stat().st_size for path in files.values())
    if size > MAX_EXPORT_BYTES:
        raise ValueError("Full preview export exceeds the 800 MB budget; no assets were uploaded.")
    # Local manifests are usable without Blob credentials or network access.
    local_urls = {key: f"/recordings/{key}.mp4" for key in files}
    local = build_catalog(service, local_urls, local=True)
    write_json(web / "public/catalog.local.json", local)
    if upload:
        urls = (publisher or BlobPublisher(web)).publish(files, progress=progress)
        # Verify remote assets before making a manifest reference them.
        import httpx
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            for key, url in urls.items():
                response = client.head(url)
                response.raise_for_status()
                if int(response.headers.get("content-length", -1)) != files[key].stat().st_size:
                    raise ValueError("Published preview size mismatch; previous catalog preserved.")
        payload = build_catalog(service, urls)
        write_json(web / "public/catalog.json", payload)
        write_json(service.config.output_dir / "collection/published.json", {
            "urls": urls, "bytes": size, "coverage": payload["coverage"],
        })
    else:
        payload = local
        # Never replace a complete hosted manifest with local-only or partial URLs.
        if not (web / "public/catalog.json").exists():
            initial = build_catalog(service, {})
            write_json(web / "public/catalog.json", initial)
    progress(f"Collection: {payload['coverage']['playable']['chunks']}/"
             f"{payload['coverage']['expected']['chunks']} chunks playable; {size} preview bytes.")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "web")
    args = parser.parse_args()
    export_collection(HighlightService(), args.output_dir.resolve(), args.upload, args.workers)


if __name__ == "__main__":
    main()
