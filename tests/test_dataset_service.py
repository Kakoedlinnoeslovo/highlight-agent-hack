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

    assert video.dataset == "sf-streets" and video.index_status == "ready"
    assert video.original_video == provider.originals[0]
    assert video.caption_model == "reported-dataset-model"
    assert video.duration == pytest.approx(1.4, abs=0.01)
    assert [chunk.original_video for chunk in video.source_chunks] == provider.originals
    assert [chunk.start_seconds for chunk in video.source_chunks] == pytest.approx([0, 0.6])
    assert [chunk.duration for chunk in video.source_chunks] == pytest.approx([0.6, 0.8])
    assert probe_media(video.local_path).duration == pytest.approx(1.4, abs=0.04)
    assert service.get_video(video.id) == video
    assert provider.segment_calls[0][1] is False  # Captions checked before publication.
    assert [original for original, _ in provider.downloads] == provider.originals
    assert all(not path.exists() for _, path in provider.downloads)
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
    # Recovery must use the registered windows, rather than silently changing
    # the source timeline when the browse metadata changes.
    provider.source["chunks"][0]["duration"] = 0.3
    downloads_before = len(provider.downloads)

    recovered = service.import_dataset_source(provider.originals[0])

    assert recovered.id == original.id and recovered.local_path == original.local_path
    assert recovered.index_status == "ready"
    assert recovered.source_chunks == original.source_chunks
    assert recovered.duration == original.duration
    service._verify_source(recovered)
    assert len(provider.downloads) == downloads_before + 2
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
    with pytest.raises(ServiceError, match="supplied SF Streets dataset"):
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
    assert len(provider.downloads) == 1 and provider.segment_calls == []
    assert_no_published_import(service)
