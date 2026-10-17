"""Collection coverage and publishing must describe only verified, playable sources."""
from __future__ import annotations

import hashlib
import shutil
import subprocess

import pytest

from highlight_agent.config import Settings
from highlight_agent.media import probe_media
from highlight_agent.models import SourceChunk, VideoRecord
from highlight_agent.service import HighlightService
from highlight_agent.storage import read_json, write_json
from scripts import export_collection as collection


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Actual preview encoding requires FFmpeg")
def test_real_preview_encoding_preserves_short_indexed_window_and_verified_cache(tmp_path):
    source = tmp_path / "partial-source.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=160x90:rate=30:duration=3.2", "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=stereo", "-t", "3.2", "-c:v", "libx264",
        "-preset", "ultrafast", "-c:a", "aac", str(source),
    ], check=True, capture_output=True)
    video = VideoRecord(id="short-source", label="Partial window", local_path=source,
                        duration=3.149, content_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    target = tmp_path / "preview.mp4"
    first = collection.encode_recording(video, target)
    info = probe_media(target)
    assert info.height == 480 and info.has_audio
    assert abs(info.duration - video.duration) <= 1 / 24 + 0.001
    before = target.stat().st_mtime_ns
    assert collection.encode_recording(video, target) == first
    assert target.stat().st_mtime_ns == before


def chunk(stream: str, index: int, *, location="san_francisco", camera="sf_streets_cam-5", count=1):
    return {
        "original_video": f"s3://supplied/{stream}/chunk-{index}.mp4",
        "stream_id": stream, "camera_id": camera, "location": location,
        "capture_type": "streets", "is_public": True, "chunk_index": index,
        "chunk_duration_sec": 5.0, "total_segments": count,
        "timeline": [{"segment_start_sec": number * 5 / count,
                      "segment_end_sec": (number + 1) * 5 / count,
                      "reasoning_content": f"{stream} caption {index}-{number}"}
                     for number in range(count)],
    }


@pytest.fixture
def service(tmp_path):
    return HighlightService(Settings(data_dir=tmp_path / "data", output_dir=tmp_path / "outputs"))


def inventory(service, chunks):
    write_json(service.config.data_dir / "catalog/inventory.json", {"chunks": chunks})


def register(service, chunks, *, video_id="full-recording", dataset="hackathon-public"):
    path = service.config.data_dir / "media" / f"{video_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"verified source media for {video_id}".encode())
    offset, sources, rows = 0.0, [], []
    for item in chunks:
        sources.append(SourceChunk(original_video=item["original_video"], start_seconds=offset,
                                   duration=item["chunk_duration_sec"],
                                   chunk_index=item["chunk_index"]))
        for segment in item["timeline"]:
            rows.append({"id": f"{video_id}-segment-{len(rows)}", "video_id": video_id,
                         "start_seconds": offset + segment["segment_start_sec"],
                         "end_seconds": offset + segment["segment_end_sec"],
                         "caption": segment["reasoning_content"],
                         "segment_number": len(rows) + 1})
        offset += item["chunk_duration_sec"]
    for row in rows:
        row["total_segments"] = len(rows)
    video = VideoRecord(id=video_id, label=video_id, local_path=path, duration=offset,
                        dataset=dataset, original_video=sources[0].original_video,
                        stream_id=chunks[0]["stream_id"], camera_id=chunks[0]["camera_id"],
                        location=chunks[0]["location"], capture_type=chunks[0]["capture_type"],
                        index_status="ready", source_chunks=sources,
                        content_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    service._save_video(video)
    write_json(service._index_path(video.id), rows)
    return video


def fake_encoder(video, destination):
    HighlightService._verify_source(video)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(f"encoded {video.id}".encode())
    return {"duration": video.duration, "size": destination.stat().st_size}


def test_catalog_reports_bounded_accumulated_normalization_deficit(service):
    chunks = [chunk("short-rounded", index) for index in range(20)]
    inventory(service, chunks)
    video = register(service, chunks)
    for index, source in enumerate(video.source_chunks):
        source.start_seconds = index * 4.98
        source.duration = 4.98
    video.duration = 20 * 4.98
    service._save_video(video)
    rows = read_json(service._index_path(video.id))
    for row, source in zip(rows, video.source_chunks, strict=True):
        row["start_seconds"] = source.start_seconds
        row["end_seconds"] = source.start_seconds + source.duration
    write_json(service._index_path(video.id), rows)

    catalog = collection.build_catalog(service, {collection.media_key(video): "/rounding.mp4"})

    assert catalog["coverage"]["complete"]
    assert catalog["coverage"]["expected"]["seconds"] == 100
    assert catalog["coverage"]["playable"]["seconds"] == pytest.approx(99.6)
    assert catalog["coverage"]["duration_tolerance_seconds"] == pytest.approx(0.4)


def test_offline_inventory_fallback_exports_all_612_chunks_and_3537_captions(service, tmp_path, monkeypatch):
    # Reproduce the saved collection's cardinalities without requiring private
    # local media, a connected backend, or the ignored inventory artifact.
    chunks, remaining = [], 612
    locations = ["toronto", "new_york", "san_francisco", "neighborhood", "indoor", "nashville", "warehouse3"]
    for stream in range(55):
        size = 12 if stream < 7 else 11
        for index in range(size):
            number = len(chunks)
            chunks.append(chunk(f"session-{stream}", index,
                                location=locations[stream % len(locations)],
                                camera=f"camera-{stream % 13}", count=5 if number < 135 else 6))
            remaining -= 1
    assert remaining == 0
    baseline = tmp_path / "offline-project/outputs/validation/public-dataset-inventory.json"
    write_json(baseline, {"chunks": chunks, "total": len(chunks)})
    monkeypatch.setattr(collection, "ROOT", tmp_path / "offline-project")

    catalog = collection.build_catalog(service, {})

    assert catalog["coverage"]["expected"] == {"recordings": 55, "chunks": 612,
        "segments": 3537, "seconds": 3060.0}
    assert catalog["coverage"]["imported"]["chunks"] == 0
    assert catalog["coverage"]["playable"]["chunks"] == 0
    assert not catalog["coverage"]["complete"]
    assert len({item["location"] for item in catalog["recordings"]}) == 7
    assert all(item["media"] is None for item in catalog["recordings"])
    assert len({segment["id"] for item in catalog["recordings"] for segment in item["segments"]}) == 3537


def test_full_recording_uses_measured_intervals_exact_provenance_and_one_media_url(service):
    chunks = [chunk("session-a", 0), chunk("session-a", 1)]
    inventory(service, chunks)
    video = register(service, chunks)
    key = collection.media_key(video)

    catalog = collection.build_catalog(service, {key: "https://media.example/full.mp4"})

    assert catalog["coverage"]["complete"]
    assert catalog["coverage"]["playable"] == catalog["coverage"]["expected"]
    record = catalog["recordings"][0]
    assert record["id"] == video.id and record["chunk_count"] == 2
    assert record["media"]["url"] == "https://media.example/full.mp4"
    assert [(row["start"], row["end"]) for row in record["segments"]] == [(0, 5), (5, 10)]
    assert record["segments"][1]["source"] == f"{chunks[1]['original_video']} @ 0–5 s"
    local = collection.build_catalog(service, {key: "https://media.example/full.mp4"}, local=True)
    assert local["recordings"][0]["media"]["url"] == f"/recordings/{key}.mp4"


def test_partial_collection_counts_only_playable_sources_and_preserves_other_captions(service):
    first, second = [chunk("session-a", 0)], [chunk("session-b", 0, location="new_york")]
    inventory(service, first + second)
    video = register(service, first)
    register(service, first, video_id="legacy-three-minutes", dataset="sf-streets")

    catalog = collection.build_catalog(service, {collection.media_key(video): "https://media.example/a.mp4"})

    assert not catalog["coverage"]["complete"]
    assert catalog["coverage"]["expected"]["chunks"] == 2
    assert catalog["coverage"]["imported"]["chunks"] == catalog["coverage"]["playable"]["chunks"] == 1
    assert len(catalog["recordings"]) == 2
    missing = next(row for row in catalog["recordings"] if row["stream_id"] == "session-b")
    assert missing["media"] is None and not missing["imported"]
    assert missing["segments"][0]["caption"] == second[0]["timeline"][0]["reasoning_content"]


@pytest.mark.parametrize("start,end", [(-1, 2), (2, 1), (0, 6), (True, 3), (0, float("nan"))])
def test_invalid_inventory_intervals_are_rejected_before_catalog_publication(service, start, end):
    item = chunk("bad-interval", 0)
    # JSON forbids NaN, so inject that case at the read boundary.
    item["timeline"][0].update(segment_start_sec=start, segment_end_sec=end)
    if isinstance(end, float) and end != end:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(collection, "_inventory", lambda _: {"chunks": [item]})
            with pytest.raises(ValueError, match="caption interval"):
                collection.build_catalog(service, {})
    else:
        inventory(service, [item])
        with pytest.raises(ValueError, match="caption interval"):
            collection.build_catalog(service, {})


def test_changed_local_source_is_searchable_but_never_marked_playable(service):
    chunks = [chunk("changed", 0)]
    inventory(service, chunks)
    video = register(service, chunks)
    video.local_path.write_bytes(b"changed after registration")

    catalog = collection.build_catalog(service, {collection.media_key(video): "https://media.example/old.mp4"})

    assert not catalog["coverage"]["complete"]
    assert catalog["coverage"]["imported"]["chunks"] == 0
    assert catalog["coverage"]["playable"]["chunks"] == 0
    assert catalog["coverage"]["failures"]
    assert catalog["recordings"][0]["segments"][0]["caption"] == chunks[0]["timeline"][0]["reasoning_content"]


def test_failed_publication_preserves_previous_hosted_manifest(service, tmp_path, monkeypatch):
    chunks = [chunk("publish", 0)]
    inventory(service, chunks)
    register(service, chunks)
    web = tmp_path / "web"
    hosted = web / "public/catalog.json"
    write_json(hosted, {"previous": "complete public manifest"})
    before = hosted.read_bytes()
    monkeypatch.setattr(collection, "encode_recording", fake_encoder)
    class FailedPublisher:
        def publish(self, files, progress):
            assert len(files) == 1
            raise RuntimeError("upload interrupted")

    with pytest.raises(RuntimeError, match="upload interrupted"):
        collection.export_collection(service, web, upload=True, publisher=FailedPublisher(), progress=lambda _: None)

    assert hosted.read_bytes() == before
    assert read_json(web / "public/catalog.local.json")["coverage"]["complete"]


def test_quota_preflight_counts_existing_objects_and_uploads_nothing(tmp_path, monkeypatch):
    media = tmp_path / "preview.mp4"
    media.write_bytes(b"12345")
    publisher = collection.BlobPublisher(tmp_path, budget=10)
    jobs = []
    def call(job):
        jobs.append(job)
        assert job["action"] == "list"
        return {"blobs": [{"pathname": "unrelated.txt", "size": 6, "url": "https://example/old"}]}
    monkeypatch.setattr(publisher, "_call", call)

    with pytest.raises(ValueError, match="quota preflight"):
        publisher.publish({"new": media}, progress=lambda _: None)

    assert jobs == [{"action": "list"}]


def test_publication_resume_skips_known_content_addressed_objects(tmp_path, monkeypatch):
    first, second = tmp_path / "first.mp4", tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    publisher = collection.BlobPublisher(tmp_path, budget=20)
    jobs = []
    def call(job):
        jobs.append(job)
        if job["action"] == "list":
            return {"blobs": [{"pathname": "recordings/first.mp4", "size": 5,
                               "url": "https://media.example/first.mp4"}]}
        assert job["path"] == str(second)
        return {"pathname": job["pathname"], "size": second.stat().st_size,
                "url": "https://media.example/second.mp4"}
    monkeypatch.setattr(publisher, "_call", call)

    urls = publisher.publish({"first": first, "second": second}, progress=lambda _: None)

    assert urls == {"first": "https://media.example/first.mp4", "second": "https://media.example/second.mp4"}
    assert [job["action"] for job in jobs] == ["list", "upload"]
    assert jobs[1]["pathname"] == "recordings/second.mp4"


@pytest.mark.parametrize("start,end", [(-1, 5), (3, 2), (0, 6)])
def test_invalid_local_index_intervals_fall_back_to_supplied_captions(service, start, end):
    chunks = [chunk("bad-local-index", 0)]
    inventory(service, chunks)
    video = register(service, chunks)
    rows = service.indexed_segments(video.id)
    rows[0].update(start_seconds=start, end_seconds=end)
    write_json(service._index_path(video.id), rows)

    catalog = collection.build_catalog(service, {collection.media_key(video): "https://media.example/invalid.mp4"})

    assert not catalog["coverage"]["complete"]
    assert catalog["coverage"]["playable"]["chunks"] == 0
    assert catalog["coverage"]["failures"]
    assert [(row["start"], row["end"]) for row in catalog["recordings"][0]["segments"]] == [(0, 5)]


def test_source_membership_order_must_match_the_inventory_timeline(service):
    chunks = [chunk("wrong-order", 0), chunk("wrong-order", 1)]
    inventory(service, chunks)
    video = register(service, list(reversed(chunks)))

    catalog = collection.build_catalog(service, {collection.media_key(video): "https://media.example/reversed.mp4"})

    assert not catalog["coverage"]["complete"]
    assert catalog["coverage"]["imported"]["chunks"] == 0
    assert catalog["recordings"][0]["media"] is None


def test_empty_inventory_is_never_reported_as_complete(service):
    inventory(service, [])
    assert not collection.build_catalog(service, {})["coverage"]["complete"]


def test_export_does_not_encode_superseded_full_import_memberships(service, tmp_path, monkeypatch):
    chunks = [chunk("new-membership", 0), chunk("new-membership", 1)]
    inventory(service, chunks)
    current = register(service, chunks)
    register(service, chunks[:1], video_id="superseded-full-import")
    encoded = []
    def encode(video, destination):
        encoded.append(video.id)
        return fake_encoder(video, destination)
    monkeypatch.setattr(collection, "encode_recording", encode)

    catalog = collection.export_collection(service, tmp_path / "web", progress=lambda _: None)

    assert encoded == [current.id]
    assert catalog["coverage"]["complete"]


def test_remote_verification_failure_preserves_previous_hosted_manifest(service, tmp_path, monkeypatch):
    import httpx

    chunks = [chunk("verify-publish", 0)]
    inventory(service, chunks)
    video = register(service, chunks)
    web = tmp_path / "web"
    hosted = web / "public/catalog.json"
    write_json(hosted, {"previous": "verified hosted manifest"})
    before = hosted.read_bytes()
    monkeypatch.setattr(collection, "encode_recording", fake_encoder)
    class Publisher:
        def publish(self, files, progress):
            return {collection.media_key(video): "https://media.example/truncated.mp4"}
    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def head(self, url):
            return httpx.Response(200, headers={"content-length": "1"},
                                  request=httpx.Request("HEAD", url))
    monkeypatch.setattr(httpx, "Client", Client)

    with pytest.raises(ValueError, match="size mismatch"):
        collection.export_collection(service, web, upload=True, publisher=Publisher(), progress=lambda _: None)

    assert hosted.read_bytes() == before
    assert not (service.config.output_dir / "collection/published.json").exists()


def test_export_size_budget_stops_before_any_publication(service, tmp_path, monkeypatch):
    chunks = [chunk("over-budget", 0)]
    inventory(service, chunks)
    register(service, chunks)
    monkeypatch.setattr(collection, "encode_recording", fake_encoder)
    monkeypatch.setattr(collection, "MAX_EXPORT_BYTES", 1)
    class ForbiddenPublisher:
        def publish(self, *args, **kwargs):
            pytest.fail("An over-budget export must stop before network publication.")

    with pytest.raises(ValueError, match="800 MB budget"):
        collection.export_collection(service, tmp_path / "web", upload=True,
                                     publisher=ForbiddenPublisher(), progress=lambda _: None)


@pytest.mark.parametrize("field,replacement", [
    ("camera_id", "replacement-camera"), ("location", "new_york"), ("capture_type", "traffic"),
])
@pytest.mark.parametrize("with_signature", [True, False])
def test_catalog_selects_current_metadata_membership_when_source_uris_are_unchanged(
    service, field, replacement, with_signature
):
    previous_chunks = [chunk("same-stream", 0)]
    current_chunks = [{**previous_chunks[0], field: replacement}]
    previous = register(service, previous_chunks, video_id="a-previous")
    current = register(service, current_chunks, video_id="z-current")
    if with_signature:
        for video, rows in ((previous, previous_chunks), (current, current_chunks)):
            first = rows[0]
            source = {**{key: first[key] for key in ("stream_id", "camera_id", "location", "capture_type")},
                      "chunks": [{"original_video": row["original_video"],
                                  "duration": row["chunk_duration_sec"],
                                  "chunk_index": row["chunk_index"],
                                  "segment_count": row["total_segments"]} for row in rows]}
            service._save_video(video.model_copy(update={
                "import_signature": service.dataset_import_signature(source),
            }))
    inventory(service, current_chunks)
    urls = {collection.media_key(previous): "https://media.example/previous.mp4",
            collection.media_key(current): "https://media.example/current.mp4"}

    catalog = collection.build_catalog(service, urls)

    assert catalog["coverage"]["complete"]
    assert catalog["recordings"][0]["id"] == current.id
    assert catalog["recordings"][0]["media"]["url"] == urls[collection.media_key(current)]
    assert catalog["recordings"][0][field] == replacement


def test_shared_recording_content_encodes_and_uploads_once_with_separate_catalog_records(
    service, tmp_path, monkeypatch,
):
    import httpx

    first, second = [chunk("same-bytes-a", 0)], [chunk("same-bytes-b", 0)]
    a = register(service, first, video_id="first-recording")
    b = register(service, second, video_id="second-recording")
    b.local_path.write_bytes(a.local_path.read_bytes())
    service._save_video(b.model_copy(update={"content_sha256": a.content_sha256}))
    inventory(service, first + second)
    encoded, published = [], {}
    def encode(video, destination):
        encoded.append(video.id)
        return fake_encoder(video, destination)
    monkeypatch.setattr(collection, "encode_recording", encode)
    class Publisher:
        def publish(self, files, progress):
            published.update(files)
            return {key: f"https://media.example/{key}.mp4" for key in files}
    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def head(self, url):
            assert len(published) == 1
            size = next(iter(published.values())).stat().st_size
            return httpx.Response(200, headers={"content-length": str(size)},
                                  request=httpx.Request("HEAD", url))
    monkeypatch.setattr(httpx, "Client", Client)

    catalog = collection.export_collection(service, tmp_path / "web", upload=True,
                                           publisher=Publisher(), progress=lambda _: None)

    assert len(encoded) == len(published) == 1
    assert catalog["coverage"]["complete"]
    assert len(catalog["recordings"]) == 2
    assert {row["id"] for row in catalog["recordings"]} == {a.id, b.id}
    assert len({row["media"]["url"] for row in catalog["recordings"]}) == 1
    assert len({row["stream_id"] for row in catalog["recordings"]}) == 2
