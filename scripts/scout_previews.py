"""Local, verified street-camera stills for the Corner Scout introduction."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import city_report as city

from highlight_agent.models import VideoRecord
from highlight_agent.service import HighlightService, ServiceError


def corner_previews(service, corners) -> list[dict]:
    """Return up to five camera cards, without inference or dataset requests.

    A thumbnail is an introductory source preview, not an audience observation.
    Unavailable or changed recordings keep their metadata and omit the image.
    """
    selected = list(corners)[:5]

    def preview(corner):
        result = {
            "camera": corner["camera"],
            "headline": corner.get("headline", ""),
            "poster": "",
        }
        video = corner.get("video")
        if service is None or not isinstance(video, VideoRecord):
            return result
        temporary = None
        try:
            # Cache reuse must not bypass verification of the registered media.
            HighlightService._verify_source(video)
            fingerprint = video.content_sha256[:12] or str(video.local_path.stat().st_mtime_ns)
            directory = Path(service.config.output_dir) / "corner-scout" / "previews"
            destination = directory / f"{video.id}-{fingerprint}.jpg"
            if not destination.is_file() or not destination.stat().st_size:
                directory.mkdir(parents=True, exist_ok=True)
                temporary = directory / f".{destination.stem}-{uuid4().hex}.jpg"
                city._thumbnail(video.local_path, min(video.duration / 2, 45), temporary)
                if not temporary.stat().st_size:
                    raise OSError("Thumbnail generation produced an empty image")
                temporary.replace(destination)
            result["poster"] = city._data_uri(destination)
        except (OSError, subprocess.SubprocessError, ServiceError):
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return result

    if not selected:
        return []
    if service is None:
        return [preview(corner) for corner in selected]
    with ThreadPoolExecutor(max_workers=min(5, len(selected))) as pool:
        return list(pool.map(preview, selected))
