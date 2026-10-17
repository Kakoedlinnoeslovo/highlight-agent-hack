"""Preview caching never turns changed or failed source media into a camera image."""

from __future__ import annotations

import base64
import hashlib
import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from highlight_agent.models import VideoRecord


@pytest.fixture
def previews(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("scout_previews")


@pytest.fixture
def registered_corner(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"registered camera recording")
    video = VideoRecord(
        id="camera-1",
        label="Camera 1",
        local_path=source,
        duration=180,
        content_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    service = SimpleNamespace(config=SimpleNamespace(output_dir=tmp_path / "output"))
    return service, {"camera": "Camera 1", "headline": "Market Street", "video": video}


def test_no_service_returns_ordered_metadata_without_media_access(previews, monkeypatch):
    monkeypatch.setattr(
        previews.HighlightService,
        "_verify_source",
        lambda *_: pytest.fail("Metadata-only preview attempted media verification"),
    )
    corners = [{"camera": f"Camera {i}", "headline": f"Street {i}"} for i in range(7)]

    assert previews.corner_previews(None, corners) == [
        {**corner, "poster": ""} for corner in corners[:5]
    ]
    assert previews.corner_previews(None, []) == []


@pytest.mark.parametrize(("duration", "seconds"), [(180, 45), (20, 10)])
def test_verified_preview_uses_correct_frame_and_reuses_hash_cache(
    previews, registered_corner, monkeypatch, duration, seconds
):
    service, corner = registered_corner
    corner["video"].duration = duration
    calls = []
    image = b"test JPEG thumbnail"

    def thumbnail(source, timestamp, destination):
        calls.append((source, timestamp))
        destination.write_bytes(image)

    monkeypatch.setattr(previews.city, "_thumbnail", thumbnail)

    first = previews.corner_previews(service, [corner])
    second = previews.corner_previews(service, [corner])

    assert calls == [(corner["video"].local_path, seconds)]
    assert first == second
    assert first[0]["poster"] == "data:image/jpeg;base64," + base64.b64encode(image).decode()
    cache = service.config.output_dir / "corner-scout" / "previews"
    assert [path.name for path in cache.iterdir()] == [
        f"camera-1-{corner['video'].content_sha256[:12]}.jpg"
    ]


@pytest.mark.parametrize("missing", [False, True])
def test_changed_or_missing_media_cannot_reuse_an_existing_thumbnail(
    previews, registered_corner, monkeypatch, missing
):
    service, corner = registered_corner
    monkeypatch.setattr(
        previews.city, "_thumbnail", lambda _, __, destination: destination.write_bytes(b"JPEG")
    )
    assert previews.corner_previews(service, [corner])[0]["poster"]
    if missing:
        corner["video"].local_path.unlink()
    else:
        corner["video"].local_path.write_bytes(b"changed recording")

    result = previews.corner_previews(service, [corner])

    assert result == [{"camera": "Camera 1", "headline": "Market Street", "poster": ""}]


@pytest.mark.parametrize(
    "failure", [OSError("ffmpeg unavailable"), subprocess.CalledProcessError(1, "ffmpeg")]
)
def test_failed_thumbnail_is_not_cached_and_a_later_request_can_retry(
    previews, registered_corner, monkeypatch, failure
):
    service, corner = registered_corner

    def broken_thumbnail(_, __, destination):
        destination.write_bytes(b"partial image")
        raise failure

    monkeypatch.setattr(previews.city, "_thumbnail", broken_thumbnail)
    assert previews.corner_previews(service, [corner])[0]["poster"] == ""
    cache = service.config.output_dir / "corner-scout" / "previews"
    assert not list(cache.iterdir())

    monkeypatch.setattr(
        previews.city, "_thumbnail", lambda _, __, destination: destination.write_bytes(b"JPEG")
    )
    assert previews.corner_previews(service, [corner])[0]["poster"]


def test_legacy_source_without_a_hash_uses_mtime_after_verification(
    previews, registered_corner, monkeypatch
):
    service, corner = registered_corner
    corner["video"].content_sha256 = ""
    verified = []
    monkeypatch.setattr(previews.HighlightService, "_verify_source", verified.append)
    monkeypatch.setattr(
        previews.city, "_thumbnail", lambda _, __, destination: destination.write_bytes(b"JPEG")
    )

    assert previews.corner_previews(service, [corner])[0]["poster"]

    assert verified == [corner["video"]]
    stamp = corner["video"].local_path.stat().st_mtime_ns
    assert (service.config.output_dir / "corner-scout" / "previews" / f"camera-1-{stamp}.jpg").is_file()
