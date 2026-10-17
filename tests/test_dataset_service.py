"""Dataset import keeps the downloaded media, measured offsets, and index aligned."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from highlight_agent.config import Settings
from highlight_agent.media import MediaError, probe_media, render_edit_plan
from highlight_agent.models import EditPlan, EditWindow
from highlight_agent.service import HighlightService, ServiceError
from highlight_agent.vast import ProviderError


def ffmpeg(*arguments: str) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *arguments],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout


@pytest.fixture(scope="module")
def dataset_media(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required for the dataset media integration checks.")
    directory = tmp_path_factory.mktemp("dataset-chunks")
    paths = []
    for index, (color, duration) in enumerate((("red", 0.6), ("blue", 0.8))):
        path = directory / f"{index}-{color}.mp4"
        ffmpeg(
            "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=30:d={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path),
        )
        paths.append(path)
    return paths


class DatasetProvider:
    def __init__(self, paths):
        self.originals = [
            "s3://supplied/sf_streets_cam-5/chunk-090000.mp4",
            "s3://supplied/sf_streets_cam-5/chunk-120000.mp4",
        ]
        self.paths = dict(zip(self.originals, paths, strict=True))
        self.source = {
            "original_video": self.originals[0],
            "label": "SF Streets test recording",
            "chunks": [
                # Both declarations are within tolerance but differ from measured
                # media: the assembled offsets must use 0.6s and 0.8s instead.
                {"original_video": self.originals[0], "duration": 0.65},
                {"original_video": self.originals[1], "duration": 0.85},
            ],
        }
        self.list_calls = 0
        self.downloads = []
        self.segment_calls = []
        self.fail_download = None
        self.fail_segments_at = None
        self.segment_limit = None

    def dataset_sources(self):
        self.list_calls += 1
        return [self.source]

    def download_dataset_source(self, original, destination):
        self.downloads.append((original, destination))
        if original == self.fail_download:
            raise ProviderError("Dataset download was incomplete. Retry the import.")
        shutil.copyfile(self.paths[original], destination)
        return destination

    def segments(self, video):
        self.segment_calls.append((video, video.local_path.exists()))
        if len(self.segment_calls) == self.fail_segments_at:
            raise ProviderError("Dataset captions do not match the requested source.")
        rows = [
            {
                "id": f"dataset-moment-{index}",
                "video_id": video.id,
                "original_video": chunk.original_video,
                "start_seconds": chunk.start_seconds,
                "end_seconds": chunk.start_seconds + chunk.duration,
                "caption": "Pedestrians cross." if index == 0 else "A bus passes.",
                "cosmos_model": "reported-dataset-model",
                "segment_number": index + 1,
                "total_segments": len(video.source_chunks),
            }
            for index, chunk in enumerate(video.source_chunks)
        ]
        return rows[: self.segment_limit]

    def upload(self, *args, **kwargs):
        pytest.fail("Importing supplied indexed media must never upload or recaption it.")


class ForbiddenInference:
    def expand(self, *args):
        pytest.fail("Dataset import must not call inference.")

    def rank(self, *args):
        pytest.fail("Dataset import must not call inference.")


def make_service(tmp_path, provider, mode="live"):
    return HighlightService(
        Settings(mode=mode, data_dir=tmp_path / "data", output_dir=tmp_path / "outputs"),
        vast_client=provider,
        inference_client=ForbiddenInference(),
    )


def assert_no_published_import(service):
    assert service.list_videos() == []
    assert not list(service.config.data_dir.glob("index/*.json"))
    assert not list(service.config.data_dir.glob("media/*"))


def rgb_at(path: Path, seconds: float) -> tuple[int, int, int]:
    raw = ffmpeg(
        "-ss", str(seconds), "-i", str(path), "-frames:v", "1",
        "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
    )
    assert len(raw) == 3
    return tuple(raw)


def test_import_assembles_exact_chunk_order_and_renders_measured_source_offsets(
    tmp_path, dataset_media
):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)

    video = service.import_dataset_source(provider.originals[0])

    assert video.dataset == "hackathon-public" and video.index_status == "ready"
    assert video.original_video == provider.originals[0]
    assert video.caption_model == "reported-dataset-model"
    assert video.duration == pytest.approx(1.4, abs=0.01)
    assert [chunk.original_video for chunk in video.source_chunks] == provider.originals
    assert [chunk.start_seconds for chunk in video.source_chunks] == pytest.approx([0, 0.6])
    assert [chunk.duration for chunk in video.source_chunks] == pytest.approx([0.6, 0.8])
    assert probe_media(video.local_path).duration == pytest.approx(1.4, abs=0.04)
    assert service.get_video(video.id) == video
    assert provider.segment_calls[0][1] is False  # Captions checked before publication.
    assert sorted(original for original, _ in provider.downloads) == sorted(provider.originals)
    assert all(path.exists() for _, path in provider.downloads)
    rows = service.indexed_segments(video.id)
    assert [(row["start_seconds"], row["end_seconds"]) for row in rows] == [
        (0, 0.6), (0.6, 1.4)
    ]
    assert rgb_at(video.local_path, 0.3)[0] > 200
    assert rgb_at(video.local_path, 0.9)[2] > 200

    # Exercise the production renderer against the imported source, reversing
    # two windows to prove the indexed offsets address the expected pixels.
    plan = EditPlan(
        id="dataset-render",
        video_id=video.id,
        source_path=video.local_path,
        source_duration=video.duration,
        prompt="Check imported dataset source offsets",
        windows=[
            EditWindow(moment_ids=[rows[1]["id"]], start_seconds=0.7, end_seconds=1.2),
            EditWindow(moment_ids=[rows[0]["id"]], start_seconds=0.1, end_seconds=0.5),
        ],
    )
    output = render_edit_plan(plan, tmp_path / "rendered")
    assert probe_media(output).duration == pytest.approx(0.9, abs=0.04)
    assert rgb_at(output, 0.2)[2] > 200
    assert rgb_at(output, 0.7)[0] > 200

    downloads_before = len(provider.downloads)
    again = service.import_dataset_source(provider.originals[0])
    assert again.id == video.id and again.index_status == "ready"
    assert len(provider.downloads) == downloads_before
    assert len(service.list_videos()) == 1


def test_import_excludes_uncaptioned_tails_and_preserves_original_media_provenance(
    tmp_path, dataset_media
):
    provider = DatasetProvider(dataset_media)
    provider.source["chunks"][0]["duration"] = 0.4
    provider.source["chunks"][1]["duration"] = 0.6
    service = make_service(tmp_path, provider)

    video = service.import_dataset_source(provider.originals[0])

    assert video.index_status == "ready"
    assert [chunk.source_duration for chunk in video.source_chunks] == pytest.approx([0.6, 0.8])
    assert [chunk.source_sha256 for chunk in video.source_chunks] == [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in dataset_media
    ]
    assert [chunk.duration for chunk in video.source_chunks] == pytest.approx([0.4, 0.6])
    assert [chunk.start_seconds for chunk in video.source_chunks] == pytest.approx([0, 0.4])
    assert video.duration == pytest.approx(1.0, abs=0.01)
    assert service.get_video(video.id) == video
    info = probe_media(video.local_path)
    assert info.duration == pytest.approx(1.0, abs=0.04)
    assert (info.width, info.height) == (1280, 720)
    assert rgb_at(video.local_path, 0.3)[0] > 200
    # The second chunk begins at 0.4s rather than after the original 0.6s:
    # retaining the first source's uncaptioned tail would show red here.
    assert rgb_at(video.local_path, 0.45)[2] > 200
    assert rgb_at(video.local_path, 0.9)[2] > 200
    rows = service.indexed_segments(video.id)
    assert [(row["start_seconds"], row["end_seconds"]) for row in rows] == [
        (0, 0.4), (0.4, 1.0)
    ]


@pytest.mark.parametrize("missing", [True, False], ids=["missing", "changed"])
def test_reimport_recovers_registered_media_and_preserves_its_exact_excerpt(
    tmp_path, dataset_media, missing
):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    original = service.import_dataset_source(provider.originals[0])
    if missing:
        original.local_path.unlink()
    else:
        original.local_path.write_bytes(b"changed local media")
    # Rebuild from verified durable checkpoints without another download.
    downloads_before = len(provider.downloads)

    recovered = service.import_dataset_source(provider.originals[0])

    assert recovered.id == original.id and recovered.local_path == original.local_path
    assert recovered.index_status == "ready"
    assert recovered.source_chunks == original.source_chunks
    assert recovered.duration == original.duration
    service._verify_source(recovered)
    assert len(provider.downloads) == downloads_before
    assert service.list_videos() == [recovered]
    assert list(recovered.local_path.parent.iterdir()) == [recovered.local_path]
    assert rgb_at(recovered.local_path, 0.3)[0] > 200
    assert rgb_at(recovered.local_path, 0.9)[2] > 200


@pytest.mark.parametrize("failure", ["download", "index", "changed_remote"])
def test_failed_recovery_preserves_registered_metadata_index_and_existing_media(
    tmp_path, dataset_media, failure
):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    original = service.import_dataset_source(provider.originals[0])
    saved_index = service._index_path(original.id).read_bytes()
    original.local_path.write_bytes(b"changed local media")
    if failure in {"download", "changed_remote"}:
        for raw in (service.config.data_dir / "catalog" / "chunks").glob("*/source.mp4"):
            raw.unlink()
    if failure == "download":
        provider.fail_download = provider.originals[1]
    elif failure == "index":
        provider.segment_limit = 1
    else:
        provider.paths[provider.originals[0]] = dataset_media[1]

    with pytest.raises((ProviderError, ServiceError)):
        service.import_dataset_source(provider.originals[0])

    assert service.get_video(original.id) == original
    assert service._index_path(original.id).read_bytes() == saved_index
    assert original.local_path.read_bytes() == b"changed local media"
    assert service.list_videos() == [original]
    assert list(original.local_path.parent.iterdir()) == [original.local_path]


@pytest.mark.parametrize("original", ["s3://supplied/not-listed.mp4", "https://example.com/video.mp4"])
def test_import_rejects_a_source_outside_the_supplied_listing(tmp_path, original):
    provider = DatasetProvider([Path("unused-one"), Path("unused-two")])
    service = make_service(tmp_path, provider)
    with pytest.raises(ServiceError, match="supplied public dataset"):
        service.import_dataset_source(original)
    assert provider.list_calls == 1
    assert provider.downloads == [] and provider.segment_calls == []
    assert_no_published_import(service)


@pytest.mark.parametrize("operation", ["list", "import"])
def test_offline_mode_rejects_dataset_access_without_provider_calls(tmp_path, operation):
    provider = DatasetProvider([Path("unused-one"), Path("unused-two")])
    service = make_service(tmp_path, provider, mode="offline_sample")
    with pytest.raises(ServiceError, match="requires live mode"):
        if operation == "list":
            service.dataset_sources()
        else:
            service.import_dataset_source(provider.originals[0])
    assert provider.list_calls == 0 and provider.downloads == []
    assert_no_published_import(service)


def test_chunk_duration_mismatch_leaves_no_registered_or_partial_recording(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    provider.source["chunks"][1]["duration"] = 1.4
    service = make_service(tmp_path, provider)
    with pytest.raises(ServiceError, match="duration does not match"):
        service.import_dataset_source(provider.originals[0])
    assert len(provider.downloads) == 2 and provider.segment_calls == []
    assert_no_published_import(service)


def test_failed_download_cleans_up_chunks_and_does_not_register_source(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    provider.fail_download = provider.originals[1]
    service = make_service(tmp_path, provider)
    with pytest.raises(ProviderError, match="download was incomplete"):
        service.import_dataset_source(provider.originals[0])
    assert len(provider.downloads) == 2 and provider.segment_calls == []
    assert_no_published_import(service)


def test_caption_validation_failure_does_not_publish_assembled_recording(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    provider.fail_segments_at = 1
    service = make_service(tmp_path, provider)
    with pytest.raises(ProviderError, match="captions do not match"):
        service.import_dataset_source(provider.originals[0])
    assert provider.segment_calls[0][1] is False
    assert_no_published_import(service)


@pytest.mark.parametrize("limit", [0, 1])
def test_incomplete_dataset_index_does_not_publish_recording(tmp_path, dataset_media, limit):
    provider = DatasetProvider(dataset_media)
    provider.segment_limit = limit
    service = make_service(tmp_path, provider)
    with pytest.raises(ServiceError, match="not completely indexed"):
        service.import_dataset_source(provider.originals[0])
    assert_no_published_import(service)


def test_assembled_duration_mismatch_does_not_publish_recording(
    tmp_path, dataset_media, monkeypatch
):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)

    def mismatched_assembly(path):
        info = probe_media(path)
        return replace(info, duration=info.duration + 0.2) if path.name == "assembled.mp4" else info

    monkeypatch.setattr("highlight_agent.service.probe_media", mismatched_assembly)
    with pytest.raises(ServiceError, match="Assembled dataset timestamps"):
        service.import_dataset_source(provider.originals[0])
    assert provider.segment_calls == []
    assert_no_published_import(service)


def test_concat_failure_cleans_up_downloads_without_registering_source(
    tmp_path, dataset_media, monkeypatch
):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    real_run = subprocess.run

    def fail_concat(command, *args, **kwargs):
        if command[0] == "ffmpeg" and "concat" in command:
            raise subprocess.CalledProcessError(1, command, stderr=b"concat failure")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr("highlight_agent.service.subprocess.run", fail_concat)
    with pytest.raises(ServiceError, match="Could not assemble"):
        service.import_dataset_source(provider.originals[0])
    assert provider.segment_calls == []
    assert_no_published_import(service)


def test_normalization_failure_cleans_up_downloads_without_registering_source(
    tmp_path, dataset_media, monkeypatch
):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    real_run = subprocess.run

    def fail_normalize(command, *args, **kwargs):
        if command[0] == "ffmpeg" and "concat" not in command:
            raise subprocess.CalledProcessError(1, command, stderr="normalization failure")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr("highlight_agent.service.subprocess.run", fail_normalize)
    with pytest.raises(MediaError, match="normalization failure"):
        service.import_dataset_source(provider.originals[0])
    assert len(provider.downloads) == 2 and provider.segment_calls == []
    assert_no_published_import(service)


def test_interrupted_import_reuses_verified_downloads_and_normalized_checkpoints(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    provider.fail_segments_at = 1
    with pytest.raises(ProviderError):
        service.import_dataset_source(provider.originals[0])
    assert_no_published_import(service)
    assert len(provider.downloads) == 2
    checkpoints = sorted((service.config.data_dir / "catalog" / "chunks").glob("*/*.mp4"))
    saved = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in checkpoints}

    provider.fail_segments_at = None
    imported = service.import_dataset_source(provider.originals[0])

    assert imported.index_status == "ready"
    assert len(provider.downloads) == 2
    raw_saved = {path: details for path, details in saved.items() if path.name == "source.mp4"}
    assert {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in raw_saved} == raw_saved
    assert all(not path.exists() for path in checkpoints if path.name != "source.mp4")


def test_changed_membership_creates_immutable_recording_and_preserves_legacy_edits(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    first = service.import_dataset_source(provider.originals[0])
    legacy = first.model_copy(update={"id": "legacy-excerpt", "dataset": "sf-streets",
                                     "import_signature": None})
    service._save_video(legacy)
    edit_path = service.config.data_dir / "edits" / "existing-edit.json"
    edit_path.parent.mkdir(parents=True)
    edit_path.write_text('{"video_id": "legacy-excerpt"}')
    before = first.local_path.read_bytes()
    provider.source["chunks"][0]["duration"] = 0.3

    expanded = service.import_dataset_source(provider.originals[0])

    assert expanded.id != first.id and expanded.import_signature != first.import_signature
    assert service.get_video(first.id) == first
    assert first.local_path.read_bytes() == before
    assert service.get_video(legacy.id) == legacy
    assert edit_path.read_text() == '{"video_id": "legacy-excerpt"}'
    assert len(service.list_videos()) == 3
    assert len(provider.downloads) == 2


def test_bulk_import_continues_after_failure_and_only_retries_unfinished_work(tmp_path, dataset_media):
    from copy import deepcopy

    provider = DatasetProvider(dataset_media)
    first = provider.source
    second = deepcopy(first)
    second["stream_id"] = "second-session-same-camera"
    second["camera_id"] = "sf_streets_cam-5"
    for index, chunk in enumerate(second["chunks"]):
        chunk["original_video"] = f"s3://supplied/second/chunk-{index}.mp4"
        provider.paths[chunk["original_video"]] = dataset_media[index]
    second["original_video"] = second["chunks"][0]["original_video"]
    def sources():
        provider.list_calls += 1
        return [first, second]
    provider.dataset_sources = sources
    provider.fail_download = second["chunks"][1]["original_video"]
    service = make_service(tmp_path, provider)

    report = service.import_datasets()

    assert not report["complete"]
    assert report["expected"]["recordings"] == 2 and report["expected"]["chunks"] == 4
    assert report["imported"]["recordings"] == 1 and report["imported"]["chunks"] == 2
    assert len(report["failures"]) == 1
    assert provider.list_calls == 1
    first_download_count = sum(uri in provider.originals for uri, _ in provider.downloads)
    provider.fail_download = None

    finished = service.import_datasets()

    assert finished["complete"] and not finished["failures"]
    assert finished["imported"]["recordings"] == finished["playable"]["recordings"] == 2
    assert sum(uri in provider.originals for uri, _ in provider.downloads) == first_download_count
    assert len(provider.segment_calls) == 2
    assert provider.list_calls == 2
    assert len(service.list_videos()) == 2
    assert (service.config.data_dir / "catalog" / "coverage.json").is_file()


def test_bulk_reports_saved_sources_missing_from_fresh_discovery(tmp_path, dataset_media):
    import json

    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    baseline = service.config.output_dir / "validation" / "public-dataset-inventory.json"
    baseline.parent.mkdir(parents=True)
    missing = "s3://supplied/missing-session/chunk.mp4"
    baseline.write_text(json.dumps({"chunks": [{"original_video": missing,
        "stream_id": "missing-session", "chunk_duration_sec": 30, "total_segments": 6}]}))

    report = service.import_datasets()

    assert not report["complete"]
    assert report["expected"]["recordings"] == 2 and report["expected"]["chunks"] == 3
    assert report["expected"]["duration_seconds"] == pytest.approx(31.5)
    assert report["missing_sources"] == [missing]
    assert report["failures"][0]["original_video"] == missing


def test_corrupt_normalized_checkpoint_is_rebuilt_without_redownloading_sources(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    provider.fail_segments_at = 1
    with pytest.raises(ProviderError):
        service.import_dataset_source(provider.originals[0])
    provider.fail_segments_at = None
    prepared = next(path for path in (service.config.data_dir / "catalog" / "chunks").glob("*/*.mp4")
                    if path.name != "source.mp4")
    prepared.write_bytes(b"interrupted or corrupted normalized output")

    recovered = service.import_dataset_source(provider.originals[0])

    assert recovered.index_status == "ready"
    assert len(provider.downloads) == 2
    service._verify_source(recovered)
    assert not prepared.exists()
    assert all(path.exists() for _, path in provider.downloads)


@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("keep", [True, False])
def test_bulk_cli_exit_status_reports_incomplete_collection(monkeypatch, capsys, complete, keep):
    from highlight_agent import cli

    class BulkService:
        def __init__(self, settings):
            pass

        def import_datasets(self, progress, *, keep_originals=False):
            assert keep_originals == keep
            progress("Checked supplied recordings")
            return {"complete": complete}

    monkeypatch.setattr(cli, "HighlightService", BulkService)
    arguments = ["import-datasets", "--all"] + (["--keep-originals"] if keep else [])
    assert cli.main(arguments) == (0 if complete else 1)
    captured = capsys.readouterr()
    assert '"complete"' in captured.out
    assert "Checked supplied recordings" in captured.err


def test_releasing_completed_originals_keeps_integrity_anchors_and_unregistered_chunks(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    video = service.import_dataset_source(provider.originals[0])
    raw_paths = [path for _, path in provider.downloads]
    anchors = {path.with_name("source.json"): path.with_name("source.json").read_bytes()
               for path in raw_paths}
    untouched = service.config.data_dir / "catalog/chunks" / ("a" * 64) / "source.mp4"
    untouched.parent.mkdir()
    untouched.write_bytes(b"unregistered unfinished download")
    source_before, index_before = video.local_path.read_bytes(), service._index_path(video.id).read_bytes()
    expected_bytes = sum(path.stat().st_size for path in raw_paths)

    released = service.release_dataset_originals(video)

    assert released == {"video_id": video.id, "released_chunks": 2, "released_bytes": expected_bytes}
    assert all(not path.exists() for path in raw_paths)
    assert {path: path.read_bytes() for path in anchors} == anchors
    assert untouched.read_bytes() == b"unregistered unfinished download"
    assert video.local_path.read_bytes() == source_before
    assert service._index_path(video.id).read_bytes() == index_before
    service._verify_source(video)


@pytest.mark.parametrize("failure", ["incomplete_index", "changed_assembled", "invalid_interval"])
def test_original_release_refuses_incomplete_or_changed_recordings(tmp_path, dataset_media, failure):
    from highlight_agent.storage import write_json

    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    video = service.import_dataset_source(provider.originals[0])
    if failure == "incomplete_index":
        write_json(service._index_path(video.id), service.indexed_segments(video.id)[:1])
    elif failure == "invalid_interval":
        rows = service.indexed_segments(video.id)
        rows[-1]["end_seconds"] = video.duration + 10
        write_json(service._index_path(video.id), rows)
    else:
        video.local_path.write_bytes(b"changed assembled source")

    with pytest.raises(ServiceError):
        service.release_dataset_originals(video)

    assert all(path.is_file() for _, path in provider.downloads)


@pytest.mark.parametrize("tampering", ["raw_bytes", "metadata_uri", "metadata_hash"])
def test_original_release_validates_every_registered_chunk_before_deleting_any(tmp_path, dataset_media, tampering):
    from highlight_agent.storage import read_json, write_json

    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    video = service.import_dataset_source(provider.originals[0])
    raw = next(path for original, path in provider.downloads if original == provider.originals[-1])
    metadata_path = raw.with_name("source.json")
    if tampering == "raw_bytes":
        raw.write_bytes(b"tampered original bytes")
    else:
        metadata = read_json(metadata_path)
        metadata["original_video" if tampering == "metadata_uri" else "source_sha256"] = "changed"
        write_json(metadata_path, metadata)

    with pytest.raises(ServiceError, match="integrity verification failed"):
        service.release_dataset_originals(video)

    assert all(path.is_file() for _, path in provider.downloads)


def test_bulk_resume_releases_already_completed_raws_without_downloads(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    video = service.import_dataset_source(provider.originals[0])
    assert all(path.is_file() for _, path in provider.downloads)

    report = service.import_datasets()

    assert report["complete"] and report["original_storage"] == "VAST"
    assert report["released_originals"]["chunks"] == 2
    assert len(provider.downloads) == 2 and len(provider.segment_calls) == 1
    assert all(not path.exists() for _, path in provider.downloads)
    assert all(path.with_name("source.json").is_file() for _, path in provider.downloads)
    assert service.get_video(video.id) == video
    again = service.import_datasets()
    assert again["complete"] and again["released_originals"] == {"chunks": 0, "bytes": 0}
    assert len(provider.downloads) == 2


def test_bulk_keep_originals_opt_out_retains_verified_downloads(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)

    report = service.import_datasets(keep_originals=True)

    assert report["complete"] and report["original_storage"] == "local"
    assert report["released_originals"] == {"chunks": 0, "bytes": 0}
    assert all(path.is_file() for _, path in provider.downloads)


@pytest.mark.parametrize("remote_changed", [False, True])
def test_released_original_hash_anchors_protect_future_recovery(tmp_path, dataset_media, remote_changed):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    video = service.import_dataset_source(provider.originals[0])
    service.release_dataset_originals(video)
    video.local_path.unlink()
    if remote_changed:
        provider.paths[provider.originals[0]] = dataset_media[1]
        with pytest.raises(ServiceError, match="dataset object changed"):
            service.import_dataset_source(provider.originals[0])
    else:
        recovered = service.import_dataset_source(provider.originals[0])
        assert recovered.id == video.id
        service._verify_source(recovered)
    assert service.get_video(video.id).source_chunks == video.source_chunks
    assert sum(uri == provider.originals[0] for uri, _ in provider.downloads) == 2
    if not remote_changed:
        assert len(provider.downloads) == 4


@pytest.mark.parametrize("race", ["before_hash", "after_hash", "before_unlink"])
def test_concurrent_original_release_treats_disappearing_raw_as_already_released(
    tmp_path, dataset_media, monkeypatch, race
):
    import highlight_agent.service as service_module

    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    video = service.import_dataset_source(provider.originals[0])
    target = provider.downloads[0][1]
    real_hash = service_module._sha256
    real_unlink = Path.unlink
    raced = False

    def disappearing_hash(path):
        nonlocal raced
        if Path(path) == target and not raced:
            raced = True
            if race == "before_hash":
                real_unlink(target)
            fingerprint = real_hash(path)
            if race == "after_hash":
                real_unlink(target)
            return fingerprint
        return real_hash(path)

    def disappearing_unlink(path, *args, **kwargs):
        nonlocal raced
        if path == target and not raced:
            raced = True
            real_unlink(target)
        return real_unlink(path, *args, **kwargs)

    if race == "before_unlink":
        monkeypatch.setattr(Path, "unlink", disappearing_unlink)
    else:
        monkeypatch.setattr(service_module, "_sha256", disappearing_hash)

    released = service.release_dataset_originals(video)

    assert raced
    assert released["video_id"] == video.id
    assert all(not raw.exists() for _, raw in provider.downloads)
    assert all(raw.with_name("source.json").is_file() for _, raw in provider.downloads)
    assert len(provider.downloads) == 2
    assert service.get_video(video.id) == video
    service._verify_source(video)
    again = service.release_dataset_originals(video)
    assert again["released_chunks"] == again["released_bytes"] == 0


@pytest.mark.parametrize("missing_interval", ["leading", "internal", "trailing"])
def test_full_import_retries_cached_index_with_unaccounted_caption_interval(
    tmp_path, dataset_media, missing_interval
):
    from highlight_agent.storage import write_json

    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    video = service.import_dataset_source(provider.originals[0])
    original_rows = service.indexed_segments(video.id)
    rows = service.indexed_segments(video.id)
    if missing_interval == "leading":
        rows[0]["start_seconds"] += 0.2
    elif missing_interval == "internal":
        rows[1]["start_seconds"] += 0.2
    else:
        rows[-1]["end_seconds"] -= 0.2
    write_json(service._index_path(video.id), rows)

    recovered = service.import_dataset_source(provider.originals[0])

    assert recovered.index_status == "ready" and recovered.id == video.id
    assert service.indexed_segments(video.id) == original_rows
    assert len(provider.segment_calls) == 2
    assert len(provider.downloads) == 2


def test_full_import_retries_cached_caption_count_even_if_remaining_slots_are_renumbered(tmp_path, dataset_media):
    from highlight_agent.storage import write_json

    provider = DatasetProvider(dataset_media)
    provider.source["segment_count"] = 2
    for item in provider.source["chunks"]:
        item["segment_count"] = 1
    service = make_service(tmp_path, provider)
    video = service.import_dataset_source(provider.originals[0])
    original_rows = service.indexed_segments(video.id)
    collapsed = original_rows[0].copy()
    collapsed.update(end_seconds=video.duration, total_segments=1)
    write_json(service._index_path(video.id), [collapsed])

    recovered = service.import_dataset_source(provider.originals[0])

    assert recovered.id == video.id and recovered.index_status == "ready"
    assert service.indexed_segments(video.id) == original_rows
    assert len(provider.segment_calls) == 2 and len(provider.downloads) == 2


def test_cleanup_failure_keeps_persisted_recording_in_imported_and_playable_totals(tmp_path, dataset_media, monkeypatch):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    def fail_cleanup(video):
        raise OSError("Original cache could not be released")
    monkeypatch.setattr(service, "release_dataset_originals", fail_cleanup)

    report = service.import_datasets()

    assert not report["complete"] and len(report["failures"]) == 1
    assert report["imported"]["recordings"] == report["playable"]["recordings"] == 1
    assert report["imported"]["chunks"] == report["playable"]["chunks"] == 2
    assert report["imported"]["duration_seconds"] == pytest.approx(1.4)
    assert service.list_videos()[0].index_status == "ready"
    service._verify_source(service.list_videos()[0])


@pytest.mark.parametrize("direction", [-1, 1])
def test_full_recording_readiness_allows_one_frame_of_normalization_rounding(tmp_path, dataset_media, direction):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    video = service.import_dataset_source(provider.originals[0])
    rows = service.indexed_segments(video.id)
    rows[1]["start_seconds"] += direction / 30
    rows[-1]["end_seconds"] -= 1 / 30

    assert service._with_index_status(video, rows).index_status == "ready"


@pytest.mark.parametrize("change", ["changed_membership", "new_recording"])
def test_single_import_refreshes_full_membership_for_editor_and_scout_without_shrinking_coverage(
    tmp_path, dataset_media, monkeypatch, change,
):
    import importlib
    from copy import deepcopy

    from app import build_app
    from highlight_agent.storage import read_json, write_json

    provider = DatasetProvider(dataset_media)
    first = provider.source
    first.update(stream_id="first-stream", camera_id="sf_streets_cam-5",
                 location="san_francisco", capture_type="streets", segment_count=2)
    for index, chunk in enumerate(first["chunks"]):
        chunk.update(chunk_index=index, segment_count=1)
    second = deepcopy(first)
    second.update(stream_id="second-stream", camera_id="sf_streets_cam-4")
    for index, chunk in enumerate(second["chunks"]):
        chunk["original_video"] = f"s3://supplied/second/chunk-{index}.mp4"
        provider.paths[chunk["original_video"]] = dataset_media[index]
    second["original_video"] = second["chunks"][0]["original_video"]
    sources = [first, second]
    def discover():
        provider.list_calls += 1
        provider.dataset_inventory = [
            {"original_video": chunk["original_video"], "is_public": True,
             **{key: source[key] for key in ("stream_id", "camera_id", "location", "capture_type")},
             "chunk_index": chunk["chunk_index"], "chunk_duration_sec": chunk["duration"],
             "total_segments": chunk["segment_count"],
             "timeline": [{"segment_start_sec": 0, "segment_end_sec": chunk["duration"],
                           "reasoning_content": "Supplied caption"}]}
            for source in sources for chunk in source["chunks"]
        ]
        return sources
    provider.dataset_sources = discover
    service = make_service(tmp_path, provider)
    missing = "s3://supplied/not-currently-indexed/chunk.mp4"
    write_json(service.config.output_dir / "validation/public-dataset-inventory.json", {
        "chunks": [{"original_video": missing, "stream_id": "missing-stream",
                    "chunk_duration_sec": 30, "total_segments": 6}],
    })
    old_first = service.import_dataset_source(first["original_video"])
    unrelated = service.import_dataset_source(second["original_video"])
    inventory_path = service.config.data_dir / "catalog/inventory.json"
    previous_inventory = read_json(inventory_path)
    if change == "changed_membership":
        first["chunks"][0]["duration"] = 0.3
        selected = first
        expected_visible = {unrelated.id}
    else:
        selected = deepcopy(first)
        selected["stream_id"] = "new-stream"
        for index, chunk in enumerate(selected["chunks"]):
            chunk["original_video"] = f"s3://supplied/new/chunk-{index}.mp4"
            provider.paths[chunk["original_video"]] = dataset_media[index]
        selected["original_video"] = selected["chunks"][0]["original_video"]
        sources.append(selected)
        expected_visible = {old_first.id, unrelated.id}

    imported = service.import_dataset_source(selected["original_video"])

    # Also repair a stale snapshot when the successful recording is already cached.
    write_json(inventory_path, previous_inventory)
    downloads = len(provider.downloads)
    assert service.import_dataset_source(selected["original_video"]).id == imported.id
    assert len(provider.downloads) == downloads
    refreshed = read_json(inventory_path)
    expected_visible.add(imported.id)
    before_build = provider.list_calls
    editor = build_app(service)
    picker = next(item for item in editor.blocks.values()
                  if getattr(item, "label", None) == "Source recording")
    assert {value for _, value in picker.choices} == expected_visible
    assert provider.list_calls == before_build
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    city = importlib.import_module("city_report")
    assert {video.id for video in city.preferred_recordings(service)} == expected_visible
    assert refreshed["expected"]["recordings"] == len(sources) + 1
    assert refreshed["expected"]["chunks"] == 2 * len(sources) + 1
    assert refreshed["expected"]["segments"] == 2 * len(sources) + 6
    assert refreshed["missing_sources"] == [missing]
    assert len(refreshed["chunks"]) == 2 * len(sources)
    assert {row["stream_id"] for row in refreshed["chunks"]} == {row["stream_id"] for row in sources}
    assert service.get_video(old_first.id) == old_first


def test_failed_single_import_preserves_the_previous_inventory_snapshot(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    original = service.import_dataset_source(provider.originals[0])
    inventory = service.config.data_dir / "catalog/inventory.json"
    previous = inventory.read_bytes()
    provider.source["chunks"][0]["duration"] = 0.3
    provider.fail_segments_at = 2

    with pytest.raises(ProviderError):
        service.import_dataset_source(provider.originals[0])

    assert inventory.read_bytes() == previous
    assert service.get_video(original.id) == original


def test_full_import_rejects_overlapping_caption_intervals_before_publication(tmp_path, dataset_media):
    provider = DatasetProvider(dataset_media)
    service = make_service(tmp_path, provider)
    real_segments = provider.segments
    def overlapping_segments(video):
        rows = real_segments(video)
        rows[1]["start_seconds"] = rows[0]["end_seconds"] - 0.3
        return rows
    provider.segments = overlapping_segments

    with pytest.raises(ServiceError, match="not completely indexed"):
        service.import_dataset_source(provider.originals[0])

    assert_no_published_import(service)
    assert all(path.is_file() for _, path in provider.downloads)


def test_concurrent_imports_keep_shared_prepared_chunks_alive_until_staging(
    tmp_path, dataset_media, monkeypatch,
):
    import sys
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from copy import deepcopy

    first_provider = DatasetProvider(dataset_media)
    second_provider = DatasetProvider(dataset_media)
    second_provider.source = deepcopy(second_provider.source)
    extra = "s3://supplied/expanded/chunk-2.mp4"
    second_provider.source["chunks"].append({"original_video": extra, "duration": 0.6})
    second_provider.paths[extra] = dataset_media[0]
    first = make_service(tmp_path, first_provider)
    second = make_service(tmp_path, second_provider)
    checkpoint_ready, allow_staging, second_preparing = (threading.Event() for _ in range(3))
    real_prepare = HighlightService._prepare_dataset_chunk
    def prepare(service, chunk, *args):
        if service is second:
            second_preparing.set()
        result = real_prepare(service, chunk, *args)
        if service is first and chunk["original_video"] == first_provider.originals[0]:
            checkpoint_ready.set()
            assert allow_staging.wait(10), "The test did not release import staging"
        return result
    monkeypatch.setattr(HighlightService, "_prepare_dataset_chunk", prepare)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first_job = workers.submit(first.import_dataset_source, first_provider.originals[0])
        try:
            assert checkpoint_ready.wait(10)
            # An independent process must also observe the import lifetime lock.
            check = subprocess.run([sys.executable, "-c",
                "import fcntl,sys\n"
                "with open(sys.argv[1], 'ab') as handle:\n"
                " try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                " except BlockingIOError: sys.exit(0)\n"
                " sys.exit(1)\n", str(first.config.data_dir / "catalog/import.lock")],
                capture_output=True, timeout=5)
            assert check.returncode == 0
            second_job = workers.submit(second.import_dataset_source, second_provider.originals[0])
            assert not second_preparing.wait(0.2)
        finally:
            allow_staging.set()
        earlier, expanded = first_job.result(timeout=20), second_job.result(timeout=20)
    assert earlier.id != expanded.id
    assert len(earlier.source_chunks) == 2 and len(expanded.source_chunks) == 3
    first._verify_source(earlier)
    second._verify_source(expanded)
    assert len(first_provider.downloads) == 2
    assert len(second_provider.downloads) == 1
    assert probe_media(expanded.local_path).duration == pytest.approx(2.0, abs=0.04)
