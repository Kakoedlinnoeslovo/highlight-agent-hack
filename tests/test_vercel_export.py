"""Publishing uses verified source bytes and excludes private local file paths."""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from highlight_agent.models import Moment, VideoRecord
from highlight_agent.service import ServiceError


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("export_vercel")
    # Source-preview generation has separate tests; exporter fixtures are not real MP4 files.
    monkeypatch.setattr(module.scout, "corner_previews", lambda *_: [])
    monkeypatch.setattr(
        module, "sample_frame",
        lambda *_: "data:image/jpeg;base64," + base64.b64encode(b"sample JPEG").decode(),
    )
    return module


def corner(tmp_path):
    source = tmp_path / "private-source.mp4"
    source.write_bytes(b"verified video")
    video = VideoRecord(
        id="abc12345", label="cam-1", local_path=source, duration=10,
        content_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    moment = Moment(id="one", video_id=video.id, start_seconds=0, end_seconds=5, caption="Dog")
    return {
        "video": video, "camera": "Camera 1", "headline": "Street", "recorded": "1 Oct",
        "frames": [{
            "segment_id": "one", "start_seconds": 0, "end_seconds": 5, "seconds": 2.5,
            "counts": {"dog": 1}, "notable": "Dog",
        }],
        "moments": {"one": moment},
    }


def test_export_publishes_matching_clips_and_removes_only_stale_managed_media(
    exporter, monkeypatch, tmp_path,
):
    item = corner(tmp_path)
    monkeypatch.setattr(exporter.scout, "load_corners", lambda _: [item])
    monkeypatch.setattr(exporter.scout.city, "_source_ref", lambda *_: "chunk.mp4 @ 0–5 s")
    calls = []

    def encode(source, start, end, target):
        calls.append((source, start, end))
        target.write_bytes(b"encoded clip")

    monkeypatch.setattr(exporter, "export_clip", encode)
    site = tmp_path / "web"
    media = site / "public" / "media"
    media.mkdir(parents=True)
    stale = media / ("deadbeef-" + "0" * 20 + ".mp4")
    stale.write_bytes(b"stale")
    stale_image = stale.with_suffix(".jpg")
    stale_image.write_bytes(b"stale image")
    unrelated = media / "user-video.mp4"
    unrelated.write_bytes(b"keep")
    unrelated_image = media / "user-image.jpg"
    unrelated_image.write_bytes(b"keep image")
    image_calls = []

    def sample(source, seconds):
        image_calls.append((source, seconds))
        return "data:image/jpeg;base64," + base64.b64encode(b"exact sampled JPEG").decode()

    monkeypatch.setattr(exporter, "sample_frame", sample)
    payload = exporter.export_site(site)
    serialized = (site / "public" / "corners.json").read_text()
    assert str(tmp_path) not in serialized
    assert "private-source" not in serialized
    assert payload["corners"][0]["frames"][0]["source"] == "chunk.mp4 @ 0–5 s"
    clip = payload["corners"][0]["frames"][0]["clip"]
    assert (site / "public" / clip.lstrip("/")).read_bytes() == b"encoded clip"
    assert calls == [(item["video"].local_path, 0, 5)]
    frame = payload["corners"][0]["frames"][0]
    assert frame["sampledAt"] == 2.5
    assert image_calls == [(item["video"].local_path, 2.5)]
    assert (site / "public" / frame["image"].lstrip("/")).read_bytes() == b"exact sampled JPEG"
    assert not stale.exists()
    assert not stale_image.exists()
    assert unrelated.exists()
    assert unrelated_image.exists()
    again = exporter.export_site(site)
    assert again["corners"][0]["frames"][0]["image"] == frame["image"]
    assert len(image_calls) == 1  # Stable image identity allows verified cache reuse.
    assert json.loads((site / "lib" / "profile-config.json").read_text())["named"]
    assert payload["corners"][0]["poster"] == ""
    assert (site / "public" / "scout_motion.css").read_bytes() == (
        exporter.ROOT / "scripts" / "scout_motion.css"
    ).read_bytes()


def test_export_publishes_verified_posters_without_private_source_paths(
    exporter, monkeypatch, tmp_path,
):
    item = corner(tmp_path)
    image = b"verified source preview JPEG"
    monkeypatch.setattr(exporter.scout, "load_corners", lambda _: [item])
    monkeypatch.setattr(exporter.scout.city, "_source_ref", lambda *_: "chunk.mp4 @ 0–5 s")
    monkeypatch.setattr(exporter, "export_clip", lambda *args: args[-1].write_bytes(b"clip"))
    calls = []

    def previews(service, corners):
        calls.append(corners)
        return [{
            "camera": "Camera 1", "headline": "Street",
            "poster": "data:image/jpeg;base64," + base64.b64encode(image).decode(),
        }]

    monkeypatch.setattr(exporter.scout, "corner_previews", previews)

    site = tmp_path / "web"
    payload = exporter.export_site(site)

    expected = f"/previews/{item['video'].id}-{item['video'].content_sha256[:12]}.jpg"
    assert payload["corners"][0]["poster"] == expected
    assert (site / "public" / expected.lstrip("/")).read_bytes() == image
    assert calls == [[item]]
    serialized = (site / "public" / "corners.json").read_text()
    assert str(tmp_path) not in serialized
    assert "private-source" not in serialized
    assert "base64" not in serialized
    assert not list((site / "public" / "previews").glob("*.tmp.jpg"))


@pytest.mark.parametrize("poster", ["", "data:image/jpeg;base64,not-valid-base64!"])
def test_missing_or_unusable_thumbnail_does_not_block_snapshot_publication(
    exporter, monkeypatch, tmp_path, poster,
):
    item = corner(tmp_path)
    monkeypatch.setattr(exporter.scout, "load_corners", lambda _: [item])
    monkeypatch.setattr(exporter, "export_clip", lambda *args: args[-1].write_bytes(b"clip"))
    monkeypatch.setattr(exporter.scout, "corner_previews", lambda *_: [{"poster": poster}])

    site = tmp_path / "web"
    payload = exporter.export_site(site)

    assert payload["corners"][0]["poster"] == ""
    assert (site / "public" / "corners.json").is_file()
    assert not (site / "public" / "previews").exists()


def test_export_rejects_changed_source_before_encoding(exporter, monkeypatch, tmp_path):
    item = corner(tmp_path)
    item["video"].local_path.write_bytes(b"changed video")
    monkeypatch.setattr(exporter.scout, "load_corners", lambda _: [item])
    monkeypatch.setattr(exporter, "export_clip", lambda *_: pytest.fail("Encoded changed source"))
    monkeypatch.setattr(
        exporter.scout, "corner_previews", lambda *_: pytest.fail("Previewed changed source")
    )
    with pytest.raises(ServiceError, match="changed"):
        exporter.export_site(tmp_path / "web")
    assert not (tmp_path / "web" / "public" / "corners.json").exists()
    assert not (tmp_path / "web" / "public" / "previews").exists()


def test_export_requires_a_segment_reference(exporter, monkeypatch, tmp_path):
    item = corner(tmp_path)
    item["moments"] = {}
    monkeypatch.setattr(exporter.scout, "load_corners", lambda _: [item])
    with pytest.raises(ValueError, match="Missing source segment"):
        exporter.export_site(tmp_path / "web")


@pytest.mark.parametrize("field", ["start_seconds", "end_seconds", "seconds"])
def test_export_rejects_observations_for_changed_windows(exporter, monkeypatch, tmp_path, field):
    item = corner(tmp_path)
    item["frames"][0][field] += 1
    monkeypatch.setattr(exporter.scout, "load_corners", lambda _: [item])
    with pytest.raises(ValueError, match="observations changed"):
        exporter.export_site(tmp_path / "web")


def test_export_includes_every_valid_sample_including_negative_frames(
    exporter, monkeypatch, tmp_path,
):
    item = corner(tmp_path)
    item["video"].duration = 25
    item["frames"], item["moments"] = [], {}
    for index in range(5):
        moment = Moment(
            id=f"sample-{index}", video_id=item["video"].id,
            start_seconds=index * 5, end_seconds=(index + 1) * 5, caption="Street",
        )
        item["moments"][moment.id] = moment
        item["frames"].append({
            "segment_id": moment.id, "start_seconds": moment.start_seconds,
            "end_seconds": moment.end_seconds,
            "seconds": (moment.start_seconds + moment.end_seconds) / 2,
            "counts": {"dog": 0 if index == 1 else 1}, "notable": "Street",
        })
    monkeypatch.setattr(exporter.scout, "load_corners", lambda _: [item])
    monkeypatch.setattr(exporter, "export_clip", lambda *args: args[-1].write_bytes(b"clip"))

    site = tmp_path / "web"
    frames = exporter.export_site(site)["corners"][0]["frames"]

    assert [frame["segment_id"] for frame in frames] == list(item["moments"])
    assert [frame["sampledAt"] for frame in frames] == [2.5, 7.5, 12.5, 17.5, 22.5]
    assert len({frame["image"] for frame in frames}) == 5
    assert all((site / "public" / frame["image"].lstrip("/")).is_file() for frame in frames)
    assert frames[1]["counts"]["dog"] == 0


@pytest.mark.parametrize("mismatch", [None, "source", "segment", "timestamp"])
def test_reviewed_crop_requires_matching_source_segment_and_sample(
    exporter, monkeypatch, tmp_path, mismatch,
):
    item = corner(tmp_path)
    key = [item["video"].content_sha256, "one", 2.5]
    if mismatch == "source":
        key[0] = "0" * 64
    elif mismatch == "segment":
        key[1] = "different-segment"
    elif mismatch == "timestamp":
        key[2] = 7.5
    focus = {"dog": {"x": 0.125, "y": 0.5, "width": 0.5, "height": 0.5}}
    monkeypatch.setattr(exporter, "REVIEWED_FOCUS", {tuple(key): focus})
    monkeypatch.setattr(exporter.scout, "load_corners", lambda _: [item])
    monkeypatch.setattr(exporter, "export_clip", lambda *args: args[-1].write_bytes(b"clip"))

    frame = exporter.export_site(tmp_path / "web")["corners"][0]["frames"][0]

    assert frame.get("focus") == (focus if mismatch is None else None)
    assert frame["image"]  # A presentation crop never replaces the full image.


def test_failed_sample_extraction_keeps_previous_snapshot_and_media(
    exporter, monkeypatch, tmp_path,
):
    item = corner(tmp_path)
    monkeypatch.setattr(exporter.scout, "load_corners", lambda _: [item])
    monkeypatch.setattr(exporter, "export_clip", lambda *args: args[-1].write_bytes(b"clip"))
    site = tmp_path / "web"
    media = site / "public" / "media"
    media.mkdir(parents=True)
    old_image = media / ("deadbeef-" + "0" * 20 + ".jpg")
    old_image.write_bytes(b"previous image")
    snapshot = site / "public" / "corners.json"
    original = json.dumps({"image": f"/media/{old_image.name}"}).encode()
    snapshot.write_bytes(original)

    def fail(*_):
        raise subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr(exporter, "sample_frame", fail)
    with pytest.raises(subprocess.CalledProcessError):
        exporter.export_site(site)

    assert snapshot.read_bytes() == original
    assert old_image.read_bytes() == b"previous image"
    assert not list(media.glob("*.tmp.jpg"))


def test_changed_source_cannot_reuse_cached_sample(exporter, monkeypatch, tmp_path):
    item = corner(tmp_path)
    monkeypatch.setattr(exporter.scout, "load_corners", lambda _: [item])
    monkeypatch.setattr(exporter, "export_clip", lambda *args: args[-1].write_bytes(b"clip"))
    site = tmp_path / "web"
    exporter.export_site(site)
    snapshot = site / "public" / "corners.json"
    original = snapshot.read_bytes()
    item["video"].local_path.write_bytes(b"changed source")
    monkeypatch.setattr(exporter, "sample_frame", lambda *_: pytest.fail("Read changed source"))

    with pytest.raises(ServiceError, match="changed"):
        exporter.export_site(site)
    assert snapshot.read_bytes() == original


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="Sample extraction requires FFmpeg")
def test_real_sample_reconstructs_vision_image_at_original_timestamp(
    exporter, monkeypatch, tmp_path,
):
    source = tmp_path / "varying.mp4"
    subprocess.run([
        "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc2=size=160x90:rate=10:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(source),
    ], check=True, capture_output=True)
    actual_extractor = importlib.import_module("vision_tags")._frame
    monkeypatch.setattr(exporter, "sample_frame", actual_extractor)
    image = tmp_path / "sample.jpg"

    exporter.export_image(source, 2.5, image)

    expected = base64.b64decode(actual_extractor(source, 2.5).split(",", 1)[1])
    other_moment = base64.b64decode(actual_extractor(source, 0.5).split(",", 1)[1])
    assert image.read_bytes() == expected
    assert image.read_bytes() != other_moment
    dimensions = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height", "-of", "json", str(image),
    ], check=True, capture_output=True, text=True)
    assert json.loads(dimensions.stdout)["streams"][0] == {"width": 1024, "height": 576}


@pytest.mark.skipif(shutil.which("node") is None, reason="Hosted scoring requires Node.js")
def test_hosted_scores_and_evidence_match_python_on_published_sample(exporter):
    root = Path(__file__).resolve().parents[1]
    sample = json.loads((root / "web/public/corners.json").read_text())
    generator = random.Random(37)
    profiles = []
    for _ in range(100):
        keys = generator.sample(list(exporter.scout.TAXONOMY), 5)
        profiles.append({key: 1 if i < 2 else 0.5 for i, key in enumerate(keys)})
    result = subprocess.run(
        ["node", "--input-type=module", "-e", """
import { readFileSync } from 'node:fs';
import { rank } from './web/public/scout.js';
const sample = JSON.parse(readFileSync('web/public/corners.json', 'utf8'));
const profiles = JSON.parse(readFileSync(0, 'utf8'));
console.log(JSON.stringify(profiles.map(weights => rank(sample.corners, weights, sample))));
"""],
        input=json.dumps(profiles), text=True, capture_output=True, check=True, cwd=root,
    )
    for weights, hosted in zip(profiles, json.loads(result.stdout), strict=True):
        local = exporter.scout.rank(sample["corners"], weights)
        for actual, expected in zip(hosted, local, strict=True):
            assert actual["corner"]["id"] == expected["corner"]["id"]
            assert actual["score"] == pytest.approx(expected["score"], abs=1e-12)
            assert actual["core_frames"] == expected["core_frames"]
            assert [item["frame"]["segment_id"] for item in actual["best"]] == [
                frame["segment_id"] for _, _, frame in expected["best"]
            ]
