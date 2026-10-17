"""Report tools keep failed model observations and changed media out of evidence."""

from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import subprocess
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
    assert [{key: value for key, value in frame.items() if key != "fingerprint"}
            for frame in updated[:2]] == frames[:2]
    assert [frame["segment_id"] for frame in updated] == [moment.id for moment in moments]
    assert all(frame["counts"]["cyclist"] == 1 for frame in updated[2:])
    assert all("error" not in frame for frame in updated)


def full_source(service, video, eligible=True):
    video = video.model_copy(update={
        "dataset": "hackathon-public", "stream_id": "reviewed-stream",
        "camera_id": "sf_streets_cam-1", "location": "san_francisco",
    })
    write_json(service.config.data_dir / "catalog" / "scouting-review.json", {
        video.stream_id: {"eligible": eligible, "note": "First, middle, last views reviewed.",
                          "source_sha256": video.content_sha256},
    })
    return video


def test_full_recordings_require_review_and_moving_views_remain_explorer_only(
    report_tools, registered_source
):
    service, video = registered_source
    video = video.model_copy(update={
        "dataset": "hackathon-public", "stream_id": "new-stream",
        "camera_id": "sf_streets_cam-1",
    })
    with pytest.raises(ValueError, match="fixed-camera review"):
        report_tools.vision.tag_video(service, None, video, workers=1)
    video = full_source(service, video, eligible=False)
    assert not report_tools.vision.city.scouting_eligible(service, video)
    video = full_source(service, video)
    assert report_tools.vision.city.scouting_eligible(service, video)
    mobile = video.model_copy(update={"camera_id": "nyc_bike_gopro-1"})
    assert not report_tools.vision.city.scouting_eligible(service, mobile)


@pytest.mark.parametrize("changed", ["source_sha256", "prompt_version", "model", "interval"])
def test_full_cache_fingerprints_reject_changed_source_prompt_model_or_interval(
    report_tools, cached_moments, monkeypatch, changed
):
    vision = report_tools.vision
    service, legacy, moments, path = cached_moments
    video = full_source(service, legacy)
    frames = [{**cache_frame(vision, moment), "fingerprint": vision._fingerprint(video, moment)}
              for moment in moments]
    manifest = {
        "model": vision.VISION_MODEL, "video_id": video.id,
        "source_sha256": video.content_sha256, "prompt_version": vision.PROMPT_VERSION,
        "frames": frames,
    }
    if changed == "interval":
        frames[0]["end_seconds"] = 4
    else:
        manifest[changed] = "old-value"
    write_json(path, manifest)
    sampled = []

    def tag_frame(*args):
        sampled.append(args[-1])
        return vision._parse(json.dumps(observation(vision, dog=1)))

    monkeypatch.setattr(vision, "tag_frame", tag_frame)
    vision.tag_video(service, None, video, workers=1)
    assert sampled == ([2.5] if changed == "interval" else [2.5, 7.5, 12.5, 17.5, 22.5])
    saved = read_json(path)
    assert saved["source_sha256"] == video.content_sha256
    assert saved["prompt_version"] == vision.PROMPT_VERSION
    assert len(vision.valid_cached_frames(video, moments, saved)) == 5


def test_tagging_saves_each_completed_observation_before_next_result(
    report_tools, cached_moments, monkeypatch
):
    vision = report_tools.vision
    service, legacy, moments, path = cached_moments
    video = full_source(service, legacy)
    real_save = vision.write_json
    snapshots = []

    def save(destination, value):
        real_save(destination, value)
        snapshots.append(sum(isinstance(frame["counts"], dict) for frame in read_json(path)["frames"]))

    monkeypatch.setattr(vision, "write_json", save)
    monkeypatch.setattr(vision, "tag_frame", lambda *_: {"counts": dict.fromkeys(vision.TAXONOMY, 0), "notable": "Street"})
    vision.tag_video(service, None, video, workers=1)
    assert snapshots == list(range(len(moments) + 1))


def test_full_imports_replace_excerpts_in_scouting_without_deleting_sources(
    report_tools, registered_source, monkeypatch
):
    service, excerpt = registered_source
    excerpt = excerpt.model_copy(update={"original_video": "s3://public/first.mp4"})
    full = full_source(service, excerpt).model_copy(update={"id": "full-source"})
    other_session = full.model_copy(update={"id": "another-session", "stream_id": "other-session",
                                           "original_video": "s3://public/second.mp4"})
    monkeypatch.setattr(service, "list_videos", lambda: [excerpt, full, other_session])
    assert [video.id for video in report_tools.vision.city.preferred_recordings(service)] == [
        "full-source", "another-session",
    ]
    assert excerpt.local_path.exists()


def test_current_inventory_excludes_immutable_superseded_versions(
    report_tools, registered_source, monkeypatch
):
    service, legacy = registered_source
    current_source = {"stream_id": "reviewed-stream", "camera_id": "sf_streets_cam-1",
                      "location": "san_francisco", "capture_type": "streets", "chunks": [
                          {"original_video": "s3://public/first.mp4", "duration": 30,
                           "chunk_index": 0, "segment_count": 6},
                      ]}
    current = full_source(service, legacy).model_copy(update={
        "id": "current", "import_signature": service.dataset_import_signature(current_source),
    })
    old = current.model_copy(update={"id": "old", "import_signature": "previous-membership"})
    write_json(service.config.data_dir / "catalog" / "inventory.json", {"sources": [current_source]})
    monkeypatch.setattr(service, "list_videos", lambda: [current, old])
    assert [video.id for video in report_tools.vision.city.preferred_recordings(service)] == ["current"]


def test_shared_camera_recordings_have_stable_display_labels(report_tools, cached_moments, monkeypatch):
    service, base, moments, _ = cached_moments
    videos = [base.model_copy(update={
        "id": f"source-{letter}", "dataset": "hackathon-public", "camera_id": "nyc_streets_cam-1",
        "location": "new_york", "stream_id": f"stream-{letter}",
        "original_video": f"s3://public/{letter}.mp4",
    }) for letter in ("b", "a")]
    vision = report_tools.vision
    for video in videos:
        write_json(service.config.data_dir / "vision" / f"{video.id}.json", {
            "model": vision.VISION_MODEL, "video_id": video.id,
            "source_sha256": video.content_sha256, "prompt_version": vision.PROMPT_VERSION,
            "frames": [{**cache_frame(vision, moment), "fingerprint": vision._fingerprint(video, moment)}
                       for moment in moments],
        })
    monkeypatch.setattr(vision.city, "scouting_videos", lambda _: videos)
    corners = report_tools.scout.load_corners(service)
    assert [(corner["camera"], corner["stream_id"]) for corner in corners] == [
        ("Camera 1 · recording 1", "stream-a"), ("Camera 1 · recording 2", "stream-b"),
    ]


def timed_corner(name, *, city="San Francisco", expected=182, positive=(0, 1, 2), failed=()):
    frames = [{"segment_id": str(i), "start": 5 * i, "end": min(5 * (i + 1), 182),
               "counts": None if i in failed else {"dog": int(i in positive)}}
              for i in range(37)]
    return {"camera": name, "city": city, "eligible": True,
            "expected_seconds": expected, "frames": frames}


def test_rank_uses_valid_seconds_and_short_final_segments(report_tools):
    rows = report_tools.scout.rank([
        timed_corner("Short final positive", positive=(0, 1, 36)),
        timed_corner("Three full positives"),
    ], {"dog": 1})
    assert rows[0]["corner"]["camera"] == "Three full positives"
    assert rows[0]["score"] == 1
    assert rows[1]["score"] == pytest.approx(12 / 15)
    assert all(row["analyzed_seconds"] == 182 and row["coverage"] == 1 for row in rows)
    assert all(row["sufficient"] for row in rows)


def test_rank_isolates_cities_and_gates_incomplete_coverage(report_tools):
    sf = timed_corner("SF", expected=205)
    ny = timed_corner("NYC", city="new_york", positive=range(37))
    default = report_tools.scout.rank([sf, ny], {"dog": 1})
    assert [row["corner"]["camera"] for row in default] == ["SF"]
    assert default[0]["score"] == 1  # NYC cannot change SF's denominator.
    assert not report_tools.scout.has_enough_evidence(default)
    assert default[0]["coverage"] == pytest.approx(182 / 205)
    new_york = report_tools.scout.rank([sf, ny], {"dog": 1}, city_name="New York")
    assert [row["corner"]["camera"] for row in new_york] == ["NYC"]
    assert report_tools.scout.has_enough_evidence(new_york)


def test_failed_frames_stay_unknown_and_short_analysis_cannot_recommend(report_tools):
    rows = report_tools.scout.rank([timed_corner("Gaps", failed=(4, 5))], {"dog": 1})
    assert rows[0]["frames"] == 35
    assert rows[0]["analyzed_seconds"] == 172
    assert rows[0]["core_frames"] == 3
    assert rows[0]["coverage"] == pytest.approx(172 / 182)
    assert not report_tools.scout.has_enough_evidence(rows)


def test_python_and_browser_duration_scoring_agree(report_tools):
    if not shutil.which("node"):
        pytest.skip("Node is required for cross-language scoring parity")
    corners = [timed_corner("A", positive=(0, 1, 36)), timed_corner("B", failed=(4,)),
               timed_corner("NY", city="New York", positive=range(37))]
    keys = ("score", "core_frames", "analyzed_seconds", "expected_seconds", "coverage", "sufficient")
    python_rows = [{key: row[key] for key in keys} for row in report_tools.scout.rank(corners, {"dog": 1})]
    script = """
import { rank } from './web/public/scout.js';
const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
const {corners, keys} = JSON.parse(chunks.join(''));
console.log(JSON.stringify(rank(corners, {dog:1}).map(row => Object.fromEntries(keys.map(key => [key,row[key]])))));
"""
    browser = subprocess.run(["node", "--input-type=module", "-e", script],
                             cwd=Path(__file__).resolve().parents[1],
                             input=json.dumps({"corners": corners, "keys": keys}),
                             text=True, capture_output=True, check=True)
    assert json.loads(browser.stdout) == python_rows


def test_city_change_cancels_all_scout_entrypoints_and_clears_previous_evidence(report_tools):
    corners = [{"camera": "SF", "city": "San Francisco", "frames": [],
                "video": SimpleNamespace(duration=180)},
               {"camera": "NYC", "city": "New York", "frames": [],
                "video": SimpleNamespace(duration=180)}]
    app = report_tools.scout.build_app(None, None, corners)
    entrypoints = {key for key, event in app.fns.items()
                   if getattr(event.fn, "__name__", None) == "run"}
    assert len(entrypoints) == 3
    assert any(set(event["cancels"]) == entrypoints for event in app.config["dependencies"])
    city_change = next(event.fn for event in app.fns.values()
                       if getattr(event.fn, "__name__", None) == "change_city")
    cleared = city_change("New York")
    assert "New York" in cleared[0]["value"]
    assert cleared[0]["visible"] is True
    assert cleared[1]["value"] == "" and cleared[1]["visible"] is False
    assert cleared[2]["visible"] is False
    assert cleared[3]["value"] is None and cleared[7]["value"] is None


@pytest.mark.parametrize("change", ["missing_review_hash", "replacement_source"])
def test_full_motion_review_is_bound_to_explicit_current_source_hash(
    report_tools, registered_source, monkeypatch, change,
):
    service, original = registered_source
    video = full_source(service, original)
    review_path = service.config.data_dir / "catalog/scouting-review.json"
    if change == "missing_review_hash":
        reviews = read_json(review_path)
        reviews[video.stream_id].pop("source_sha256")
        write_json(review_path, reviews)
    else:
        video.local_path.write_bytes(b"replacement footage with the same stream identity")
        video = video.model_copy(update={
            "content_sha256": hashlib.sha256(video.local_path.read_bytes()).hexdigest(),
        })
    monkeypatch.setattr(report_tools.vision, "tag_frame",
                        lambda *_: pytest.fail("Unreviewed source reached vision inference"))
    monkeypatch.setattr(report_tools.vision.city, "_moments",
                        lambda *_: pytest.fail("Unreviewed source loaded caption evidence"))

    assert not report_tools.vision.city.scouting_eligible(service, video)
    with pytest.raises(ValueError, match="fixed-camera review"):
        report_tools.vision.tag_video(service, None, video, workers=1)
