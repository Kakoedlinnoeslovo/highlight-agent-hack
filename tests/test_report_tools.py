"""Report tools keep failed model observations and changed media out of evidence."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from highlight_agent.config import Settings
from highlight_agent.models import Moment, VideoRecord
from highlight_agent.service import HighlightService, ServiceError
from highlight_agent.storage import read_json, write_json


@pytest.fixture
def report_tools(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return SimpleNamespace(
        vision=importlib.import_module("vision_tags"),
        scout=importlib.import_module("corner_scout"),
    )


def observation(vision, **changes):
    return {**dict.fromkeys(vision.TAXONOMY, 0), "notable": "No distinctive subject", **changes}


@pytest.mark.parametrize("invalid", [None, "unknown", True, -1, 1.5])
def test_vision_rejects_invalid_counts(report_tools, invalid):
    with pytest.raises(ValueError, match="nonnegative integer"):
        report_tools.vision._parse(json.dumps(observation(report_tools.vision, dog=invalid)))


@pytest.mark.parametrize("content", ["I cannot process this image", "{}", '{"dog": 0}'])
def test_vision_rejects_missing_observations(report_tools, content):
    with pytest.raises(ValueError, match="every audience count"):
        report_tools.vision._parse(content)


def fake_vision_client(monkeypatch, vision, responses):
    replies = iter(responses)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=next(replies)))]
        )

    monkeypatch.setattr(vision, "_frame", lambda *_: "data:image/jpeg;base64,test")
    monkeypatch.setattr(vision.time, "sleep", lambda *_: None)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def test_vision_retries_bad_output_and_preserves_valid_zero_frame(report_tools, monkeypatch):
    vision = report_tools.vision
    client, calls = fake_vision_client(
        monkeypatch, vision, ["Model refusal", json.dumps(observation(vision))]
    )

    result = vision.tag_frame(client, Path("unused.mp4"), 2.5)

    assert len(calls) == 2
    assert result["counts"] == dict.fromkeys(vision.TAXONOMY, 0)
    assert "error" not in result
    ranking = report_tools.scout.rank([{"camera": "Camera 1", "frames": [result]}], {"dog": 1})
    assert ranking[0]["frames"] == 1
    assert ranking[0]["core_frames"] == 0


def test_vision_records_repeated_bad_output_as_a_gap(report_tools, monkeypatch):
    vision = report_tools.vision
    client, calls = fake_vision_client(monkeypatch, vision, ["Model refusal", "{}"])

    result = vision.tag_frame(client, Path("unused.mp4"), 2.5)

    assert len(calls) == 2
    assert result["counts"] is None
    assert result["error"] == "ValueError"


@pytest.fixture
def registered_source(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original registered video")
    video = VideoRecord(
        id="camera-1",
        label="SF Streets cam-1",
        local_path=source,
        duration=180,
        dataset="sf-streets",
        index_status="ready",
        content_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    service = HighlightService(Settings(data_dir=tmp_path / "data", output_dir=tmp_path / "out"))
    return service, video


@pytest.mark.parametrize("cached", [False, True])
def test_tagging_rejects_changed_media_before_inference_or_cache(
    report_tools, registered_source, cached
):
    service, video = registered_source
    if cached:
        write_json(
            service.config.data_dir / "vision" / f"{video.id}.json",
            {"model": report_tools.vision.VISION_MODEL, "frames": []},
        )
    video.local_path.write_bytes(b"a different street recording")

    with pytest.raises(ServiceError, match="source file changed"):
        report_tools.vision.tag_video(service, None, video, workers=1)


@pytest.mark.parametrize("missing", [False, True])
def test_scout_rechecks_loaded_media_before_inference(report_tools, registered_source, missing):
    service, video = registered_source
    corners = [{"video": video}]
    if missing:
        video.local_path.unlink()
    else:
        video.local_path.write_bytes(b"a different street recording")

    with pytest.raises(ServiceError, match="source is missing|source file changed"):
        report_tools.scout.scout(service, None, corners, "bike repair shop")


def test_evidence_rechecks_media_after_scouting(report_tools, registered_source):
    service, video = registered_source
    row = {"corner": {"video": video}, "best": [(1, True, {})]}
    video.local_path.write_bytes(b"a different street recording")

    with pytest.raises(ServiceError, match="source file changed"):
        report_tools.scout.evidence_reel(row, "bike repair shop", {}, service.config.output_dir)


def cache_frame(vision, moment, **changes):
    return {
        "segment_id": moment.id,
        "start_seconds": moment.start_seconds,
        "end_seconds": moment.end_seconds,
        "seconds": (moment.start_seconds + moment.end_seconds) / 2,
        "counts": dict.fromkeys(vision.TAXONOMY, 0),
        "notable": "No distinctive subject",
        **changes,
    }


@pytest.fixture
def cached_moments(report_tools, registered_source, monkeypatch):
    service, video = registered_source
    moments = [
        Moment(
            id=f"segment-{i}",
            video_id=video.id,
            start_seconds=5 * i,
            end_seconds=5 * (i + 1),
            caption="Pedestrians on the sidewalk.",
        )
        for i in range(5)
    ]
    monkeypatch.setattr(report_tools.vision.city, "_moments", lambda *_: moments)
    path = service.config.data_dir / "vision" / f"{video.id}.json"
    return service, video, moments, path


def test_tagging_reuses_successful_cache_without_model_calls(report_tools, cached_moments, monkeypatch):
    vision = report_tools.vision
    service, video, moments, path = cached_moments
    frames = [cache_frame(vision, moment) for moment in moments]
    frames[1]["counts"]["dog"] = 2
    write_json(path, {"model": vision.VISION_MODEL, "video_id": video.id, "frames": frames})
    before = path.read_bytes()
    monkeypatch.setattr(vision, "tag_frame", lambda *_: pytest.fail("Valid cache called inference"))

    assert vision.tag_video(service, None, video, workers=1) == path
    assert path.read_bytes() == before


def test_tagging_retries_only_failed_missing_and_invalid_cache(
    report_tools, cached_moments, monkeypatch
):
    vision = report_tools.vision
    service, video, moments, path = cached_moments
    frames = [cache_frame(vision, moment) for moment in moments[:4]]
    frames[1]["counts"]["dog"] = 2
    frames[2].update(counts=None, error="RateLimitError")
    frames[3]["counts"]["dog"] = "unknown"
    write_json(path, {"model": vision.VISION_MODEL, "video_id": video.id, "frames": frames})
    sampled = []

    def tag_frame(client, source, seconds):
        sampled.append(seconds)
        return vision._parse(json.dumps(observation(vision, cyclist=1)))

    monkeypatch.setattr(vision, "tag_frame", tag_frame)

    vision.tag_video(service, None, video, workers=1)

    assert sampled == [12.5, 17.5, 22.5]
    updated = read_json(path)["frames"]
    assert updated[:2] == frames[:2]
    assert [frame["segment_id"] for frame in updated] == [moment.id for moment in moments]
    assert all(frame["counts"]["cyclist"] == 1 for frame in updated[2:])
    assert all("error" not in frame for frame in updated)
