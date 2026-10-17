"""Validate hosted VSS contracts with actual HTTP request serialization."""

from __future__ import annotations

import json
import threading
from dataclasses import replace

import httpx
import pytest

from highlight_agent.config import Settings
from highlight_agent.models import Moment, SourceChunk, VideoRecord
from highlight_agent.service import HighlightService
from highlight_agent.vast import GAMEPLAY_CAPTION_PROMPT, ProviderError, VASTClient

ORIGINAL = "s3://uploads/team/recording.mp4"


@pytest.fixture
def video(tmp_path) -> VideoRecord:
    path = tmp_path / "recording.mp4"
    path.write_bytes(b"local recording payload")
    return VideoRecord(
        id="source-1",
        label="Driving session",
        local_path=path,
        duration=30,
        original_video=ORIGINAL,
        index_status="ready",
    )


def settings(**kwargs) -> Settings:
    return replace(
        Settings(vast_base_url="https://team.example", vast_access_token="private-token"), **kwargs
    )


def client(handler, **kwargs) -> VASTClient:
    return VASTClient(settings(**kwargs), httpx.Client(transport=httpx.MockTransport(handler)))


def segment(**kwargs) -> dict:
    row = {
        "original_video": ORIGINAL,
        "source": "s3://segments/team/recording_1.mp4",
        "reasoning_content": "A car lands upright and continues driving.",
        "segment_start_sec": 4,
        "segment_end_sec": 8,
        "duration": 4,
        "similarity_score": 0.82,
        "segment_number": 1,
        "total_segments": 3,
        "cosmos_model": "nvidia/Cosmos-Reason2-8B",
    }
    row.update(kwargs)
    return row


def test_ssh_tunnel_preserves_ingress_host_during_login_and_authenticated_requests():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        assert request.url.host == "127.0.0.1"
        if request.headers.get("host") != "video-lab-team-37.cosmos.vastdata.com":
            return httpx.Response(404)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "tunnel-token"})
        assert request.headers["authorization"] == "Bearer tunnel-token"
        return httpx.Response(200, json={"synthesis": {"cosmos_model": "configured-model"}})

    adapter = client(
        handler,
        vast_base_url="http://127.0.0.1:18737",
        vast_host_header="video-lab-team-37.cosmos.vastdata.com",
        vast_access_token="",
        vast_username="team",
        vast_password="private-password",
    )
    assert adapter._config()["synthesis"]["cosmos_model"] == "configured-model"
    assert paths == ["/api/v1/auth/login", "/api/v1/config"]


def test_upload_discovers_bucket_before_upload_and_uses_json_login(video):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            assert json.loads(request.content) == {
                "username": "team",
                "password": "private-password",
            }
            return httpx.Response(200, json={"access_token": "new-token"})
        assert request.headers["authorization"] == "Bearer new-token"
        if request.url.path.endswith("/config"):
            return httpx.Response(
                200,
                json={
                    "s3": {"s3_upload_bucket": "uploads"},
                    "synthesis": {"cosmos_model": "configured-synthesis-model"},
                },
            )
        assert request.url.path.endswith("/videos/upload")
        assert "multipart/form-data" in request.headers["content-type"]
        assert b"local recording payload" in request.content
        assert GAMEPLAY_CAPTION_PROMPT.encode() in request.content
        assert len(GAMEPLAY_CAPTION_PROMPT) <= 800
        return httpx.Response(200, json={"success": True, "object_key": "team/recording.mp4"})

    adapter = client(
        handler, vast_access_token="", vast_username="team", vast_password="private-password"
    )
    assert adapter.upload(video) == ORIGINAL
    assert paths == ["/api/v1/auth/login", "/api/v1/config", "/api/v1/videos/upload"]
    assert adapter.deployment_model == "configured-synthesis-model"
    assert not adapter.raw_models


def test_upload_supports_canonical_object_key_and_explicit_bucket(video):
    def handler(request):
        assert request.url.path == "/api/v1/videos/upload"
        return httpx.Response(200, json={"success": True, "object_key": ORIGINAL})

    assert (
        client(
            handler, vast_base_url="https://team.example/api/v1/", vast_upload_bucket="uploads"
        ).upload(video)
        == ORIGINAL
    )


def test_upload_can_caption_a_non_driving_game_without_the_driving_prompt(video):
    prompt = "Describe visible Fortnite gameplay, combat, building, and player outcomes."

    def handler(request):
        assert request.url.path == "/api/v1/videos/upload"
        assert prompt.encode() in request.content
        assert GAMEPLAY_CAPTION_PROMPT.encode() not in request.content
        return httpx.Response(200, json={"success": True, "object_key": ORIGINAL})

    assert (
        client(handler, vast_upload_bucket="uploads").upload(video, caption_prompt=prompt)
        == ORIGINAL
    )


def test_bucket_failure_prevents_remote_upload_and_hides_server_detail(video):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(500, json={"detail": "private-password private-token"})

    with pytest.raises(ProviderError) as error:
        client(handler).upload(video)
    assert paths == ["/api/v1/config"]
    assert "private-password" not in str(error.value) and "private-token" not in str(error.value)


def test_upload_refreshes_unauthorized_token_once_and_rewinds_file(video):
    uploads = []
    login_count = 0

    def handler(request):
        nonlocal login_count
        if request.url.path.endswith("/auth/login"):
            login_count += 1
            return httpx.Response(200, json={"access_token": "refreshed"})
        uploads.append(request.content)
        assert b"local recording payload" in request.content
        if len(uploads) == 1:
            return httpx.Response(401, json={"detail": "expired"})
        assert request.headers["authorization"] == "Bearer refreshed"
        return httpx.Response(200, json={"success": True, "object_key": "team/recording.mp4"})

    adapter = client(
        handler,
        vast_username="team",
        vast_password="private-password",
        vast_upload_bucket="uploads",
    )
    assert adapter.upload(video) == ORIGINAL
    assert len(uploads) == 2 and login_count == 1


def test_search_exact_source_filter_and_stable_normalized_ids(video):
    def handler(request):
        assert request.url.path == "/api/v1/tools/search"
        assert json.loads(request.content) == {
            "query": "upright car landings",
            "top_k": 16,
            "metadata_filters": {"original_video": ORIGINAL},
            "llm_top_n": 1,
            "include_public": False,
            "min_similarity": 0.1,
        }
        return httpx.Response(
            200,
            json={
                "results": [
                    segment(
                        original_video="s3://someone-else/video.mp4", cosmos_model="foreign-model"
                    ),
                    segment(),
                    segment(),
                ]
            },
        )

    adapter = client(handler)
    first = adapter.search(video, "upright car landings")
    second = adapter.search(video, "upright car landings")
    assert len(first) == 1 and first[0].id == second[0].id
    assert (first[0].start_seconds, first[0].end_seconds) == (4, 8)
    assert first[0].score == pytest.approx(8.2)
    assert first[0].evidence_type == "caption_ranked"
    assert adapter.raw_models == {"nvidia/Cosmos-Reason2-8B"}


@pytest.mark.parametrize("status", [404, 405])
def test_search_falls_back_only_for_absent_tool_route(video, status):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/tools/search"):
            return httpx.Response(status)
        return httpx.Response(200, json={"results": [segment()]})

    assert len(client(handler).search(video, "vehicle crashes")) == 1
    assert paths == ["/api/v1/tools/search", "/api/v1/search"]


def test_search_does_not_hide_deployment_error_by_falling_back(video):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(500)

    with pytest.raises(ProviderError, match="HTTP 500"):
        client(handler).search(video, "crash")
    assert paths == ["/api/v1/tools/search"]


@pytest.mark.parametrize(
    "bad_fields",
    [
        {"segment_start_sec": None},
        {"segment_start_sec": "NaN"},
        {"segment_end_sec": "inf"},
        {"segment_start_sec": -1},
        {"segment_end_sec": 31},
        {"segment_end_sec": 4},
        {"segment_end_sec": None, "duration": None},
        {"reasoning_content": ""},
        {"similarity_score": "NaN"},
    ],
)
def test_search_rejects_unknown_or_unsafe_caption_timestamps(video, bad_fields):
    adapter = client(lambda request: httpx.Response(200, json={"results": [segment(**bad_fields)]}))
    with pytest.raises(ProviderError):
        adapter.search(video, "crashes")


def test_missing_end_can_use_measured_segment_duration(video):
    row = segment(segment_end_sec=None, duration=3.25)
    adapter = client(lambda request: httpx.Response(200, json={"results": [row]}))
    assert adapter.search(video, "landings")[0].end_seconds == 7.25


def test_segments_return_normalized_safe_fields_and_readiness_metadata(video):
    row = segment()
    row.update({"api_key": "never-save-this", "extra_metadata": "private arbitrary metadata"})

    def handler(request):
        assert request.url.path == "/api/v1/tools/segments"
        assert request.url.params["original_video"] == ORIGINAL
        return httpx.Response(200, json={"original_video": ORIGINAL, "segments": [row]})

    adapter = client(handler)
    rows = adapter.segments(video)
    assert rows[0]["segment_number"] == 1 and rows[0]["total_segments"] == 3
    assert rows[0]["original_video"] == ORIGINAL
    moment = Moment.model_validate(
        {key: value for key, value in rows[0].items() if key in Moment.model_fields}
    )
    assert moment.caption == row["reasoning_content"]
    assert "api_key" not in rows[0] and "extra_metadata" not in rows[0]
    assert rows[0]["cosmos_model"] in adapter.raw_models


def test_search_caps_results_after_same_source_filter(video):
    rows = [segment(original_video="s3://foreign/video.mp4")]
    rows.extend(
        segment(
            source=f"s3://segments/team/{index}.mp4",
            segment_start_sec=index,
            segment_end_sec=index + 0.5,
        )
        for index in range(20)
    )
    adapter = client(lambda request: httpx.Response(200, json={"results": rows}))
    assert len(adapter.search(video, "crashes", top_k=16)) == 16


def test_timeouts_and_auth_failures_are_actionable_and_do_not_leak_secrets(video):
    def timeout(request):
        raise httpx.ReadTimeout("private-token in internal URL", request=request)

    with pytest.raises(ProviderError, match="timed out") as error:
        client(timeout).search(video, "crashes")
    assert "private-token" not in str(error.value)
    adapter = client(lambda request: httpx.Response(401, json={"detail": "private-token"}))
    with pytest.raises(ProviderError, match="authentication") as error:
        adapter.search(video, "crashes")
    assert "private-token" not in str(error.value)


def test_doctor_reports_models_as_configuration_and_never_exports_config():
    def handler(request):
        if request.url.path == "/openapi.json":
            return httpx.Response(
                200,
                json={
                    "paths": {
                        "/api/v1/tools/search": {},
                        "/api/v1/tools/segments": {},
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "s3": {"s3_upload_bucket": "uploads", "s3_secret_key": "secret-bucket-key"},
                "synthesis": {
                    "cosmos_model": "configured-only",
                    "nvidia_api_key": "secret-nvidia-key",
                },
                "private_config": "do-not-include",
            },
        )

    adapter = client(handler)
    report = adapter.doctor()
    assert report["schema_available"] and report["config_available"]
    assert report["configured_synthesis_model"] == "configured-only"
    assert report["observed_caption_models"] == []
    assert report["supported_endpoints"] == ["/tools/segments", "/tools/search"]
    dumped = json.dumps(report)
    assert "not proof" in dumped
    for secret in (
        "secret-bucket-key",
        "secret-nvidia-key",
        "do-not-include",
        "private-token",
        "https://team.example",
    ):
        assert secret not in dumped


def test_unknown_response_shape_and_unsafe_base_url_fail_clearly(video):
    adapter = client(lambda request: httpx.Response(200, json={"chunk_results": []}))
    with pytest.raises(ProviderError, match="no results list"):
        adapter.search(video, "crash")
    with pytest.raises(ProviderError, match="embedded credentials"):
        VASTClient(settings(vast_base_url="https://user:secret@team.example?key=secret"))


def test_caption_model_evidence_never_leaks_between_sources(video):
    second_original = "s3://uploads/team/another-recording.mp4"
    second_video = video.model_copy(update={"id": "source-2", "original_video": second_original})

    def handler(request):
        original = json.loads(request.content)["metadata_filters"]["original_video"]
        row = segment(original_video=original)
        if original == second_original:
            row.pop("cosmos_model")
        return httpx.Response(200, json={"results": [row]})

    adapter = client(handler)
    adapter.search(video, "crashes")
    adapter.search(second_video, "landings")
    assert adapter.models_for_source(ORIGINAL) == {"nvidia/Cosmos-Reason2-8B"}
    assert adapter.models_for_source(second_original) == set()
    assert adapter.raw_models == {"nvidia/Cosmos-Reason2-8B"}
    copied = adapter.models_for_source(ORIGINAL)
    copied.add("caller-mutated")
    assert adapter.models_for_source(ORIGINAL) == {"nvidia/Cosmos-Reason2-8B"}


def dataset_row(index=0, **changes):
    row = {
        "original_video": f"s3://public/sf5/chunk-{index}.mp4",
        "is_public": True,
        "location": "san_francisco",
        "capture_type": "streets",
        "camera_id": "sf_streets_cam-5",
        "stream_id": "sf5-session",
        "chunk_index": index,
        "chunk_duration_sec": 30,
        "total_segments": 3,
    }
    row.update(changes)
    return row


def excerpt(video, count=2):
    return VideoRecord(
        **{
            **video.model_dump(),
            "duration": 30 * count,
            "dataset": "sf-streets",
            "original_video": dataset_row()["original_video"],
            "source_chunks": [
                SourceChunk(
                    original_video=dataset_row(index)["original_video"],
                    start_seconds=30 * index,
                    duration=30,
                )
                for index in range(count)
            ],
        }
    )


def test_dataset_browse_paginates_every_public_collection_and_retains_all_chunks():
    # The early page is intentionally unsorted, duplicated, and mixed with
    # recordings that match some, but not all, SF Streets collection fields.
    pages = {
        0: [dataset_row(2), dataset_row(0), dataset_row(0)],
        3: [
            dataset_row(1),
            dataset_row(3),
            dataset_row(4),
            dataset_row(5),
            dataset_row(6),
            dataset_row(7, is_public=False),
            dataset_row(8, location="new_york", stream_id="ny-session"),
            dataset_row(9, capture_type="traffic_simulation", stream_id="sim-session"),
            dataset_row(10, camera_id="unrelated-camera", stream_id="other-session"),
        ],
    }
    offsets = []

    def handler(request):
        assert request.url.path == "/api/v1/videos/explore"
        assert request.headers["authorization"] == "Bearer private-token"
        params = dict(request.url.params)
        offset = int(params.pop("offset"))
        assert params == {
            "scope": "public",
            "indexed": "complete",
            "limit": "100",
        }
        offsets.append(offset)
        return httpx.Response(200, json={"chunks": pages[offset], "total": 12})

    sources = client(handler).dataset_sources()
    assert offsets == [0, 3]
    assert len(sources) == 4
    source = next(item for item in sources if item["stream_id"] == "sf5-session")
    assert source["original_video"] == dataset_row()["original_video"]
    assert source["duration"] == 210
    assert source["segment_count"] == 21
    assert [row["chunk_index"] for row in source["chunks"]] == list(range(7))
    assert source["location"] == "san_francisco"
    assert "San Francisco" in source["label"]


def test_dataset_browse_splits_capture_gaps_and_keeps_other_streams_separate():
    rows = [
        dataset_row(4),
        dataset_row(6),
        dataset_row(1, stream_id="another-session", camera_id="sf_streets_cam-2"),
    ]
    sources = client(
        lambda request: httpx.Response(200, json={"chunks": rows, "total": 3})
    ).dataset_sources()
    assert len(sources) == 3
    assert sorted(source["duration"] for source in sources) == [30, 30, 30]
    assert all(len(source["chunks"]) == 1 for source in sources)


@pytest.mark.parametrize(
    "payload",
    [
        {"chunks": [], "total": 1},
        {"chunks": [], "total": 0, "table_available": False},
        {"chunks": [dataset_row(chunk_duration_sec="NaN")], "total": 1},
        {"chunks": [dataset_row(original_video="https://youtube.example/video")], "total": 1},
    ],
)
def test_dataset_browse_rejects_incomplete_pagination_or_unusable_index_metadata(payload):
    with pytest.raises(ProviderError):
        client(lambda request: httpx.Response(200, json=payload)).dataset_sources()


def test_dataset_download_uses_exact_source_query_token_and_ingress_host(tmp_path):
    original = dataset_row()["original_video"]
    payload = b"complete supplied dataset media"

    def handler(request):
        assert request.url.host == "127.0.0.1"
        assert request.url.path == "/api/v1/videos/stream"
        assert dict(request.url.params) == {"source": original, "token": "private-token"}
        assert request.headers["authorization"] == "Bearer private-token"
        assert request.headers["host"] == "video-lab-team-37.cosmos.vastdata.com"
        return httpx.Response(200, content=payload)

    destination = tmp_path / "media" / "dataset.mp4"
    adapter = client(
        handler,
        vast_base_url="http://127.0.0.1:18737",
        vast_host_header="video-lab-team-37.cosmos.vastdata.com",
    )
    assert adapter.download_dataset_source(original, destination) == destination
    assert destination.read_bytes() == payload
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize(
    ("status", "body", "length"),
    [
        (206, b"partial media", None),
        (200, b"", None),
        (200, b"truncated media", "1000"),
        (500, b"private-token server detail", None),
    ],
)
def test_dataset_download_failure_preserves_existing_file_and_cleans_temporary_data(
    tmp_path, status, body, length
):
    destination = tmp_path / "dataset.mp4"
    destination.write_bytes(b"existing complete media")
    headers = {"content-length": length} if length is not None else {}
    adapter = client(lambda request: httpx.Response(status, content=body, headers=headers))
    with pytest.raises(ProviderError) as error:
        adapter.download_dataset_source(dataset_row()["original_video"], destination)
    assert "private-token" not in str(error.value)
    assert destination.read_bytes() == b"existing complete media"
    assert list(tmp_path.iterdir()) == [destination]


def test_dataset_download_refreshes_expired_query_token_without_reusing_it(tmp_path):
    tokens = []

    def handler(request):
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "refreshed-token"})
        tokens.append(request.url.params["token"])
        if len(tokens) == 1:
            return httpx.Response(401)
        assert request.headers["authorization"] == "Bearer refreshed-token"
        return httpx.Response(200, content=b"complete media")

    adapter = client(handler, vast_username="team", vast_password="private-password")
    destination = tmp_path / "dataset.mp4"
    adapter.download_dataset_source(dataset_row()["original_video"], destination)
    assert tokens == ["private-token", "refreshed-token"]
    assert destination.read_bytes() == b"complete media"


def test_dataset_download_hides_tokenized_network_errors_and_leaves_no_partial_file(tmp_path):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("https://team.example/videos/stream?token=private-token")

    adapter = client(lambda request: httpx.Response(200, stream=BrokenStream()))
    destination = tmp_path / "dataset.mp4"
    with pytest.raises(ProviderError, match="download failed") as error:
        adapter.download_dataset_source(dataset_row()["original_video"], destination)
    assert "private-token" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_dataset_search_filters_each_chunk_before_offsets_and_global_ranking(video):
    source = excerpt(video)
    queries = []

    def handler(request):
        body = json.loads(request.content)
        original = body["metadata_filters"]["original_video"]
        queries.append(original)
        assert body["include_public"] is True
        assert body["query"] == "pedestrians crossing"
        assert body["top_k"] == 2
        index = next(
            i for i, chunk in enumerate(source.source_chunks) if chunk.original_video == original
        )
        row = segment(original_video=original, similarity_score=0.6 + index * 0.3)
        foreign = segment(
            original_video=source.source_chunks[1 - index].original_video,
            cosmos_model="foreign-model",
            similarity_score=1,
        )
        lower_ranked = segment(
            original_video=original,
            source="s3://segments/lower-ranked.mp4",
            segment_start_sec=8,
            segment_end_sec=12,
            similarity_score=0.2,
        )
        return httpx.Response(200, json={"results": [foreign, row, row, lower_ranked]})

    adapter = client(handler)
    moments = adapter.search(source, "pedestrians crossing", top_k=2)
    assert set(queries) == {chunk.original_video for chunk in source.source_chunks}
    assert [(row.start_seconds, row.end_seconds) for row in moments] == [(34, 38), (4, 8)]
    assert [row.score for row in moments] == pytest.approx([9, 6])
    assert len({row.id for row in moments}) == 2
    assert adapter.raw_models == {"nvidia/Cosmos-Reason2-8B"}


def test_dataset_search_runs_at_most_three_source_requests_together(video):
    source = excerpt(video, count=6)
    barrier = threading.Barrier(3, timeout=3)
    lock = threading.Lock()
    active = 0
    peak = 0

    def handler(request):
        nonlocal active, peak
        original = json.loads(request.content)["metadata_filters"]["original_video"]
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait()
        with lock:
            active -= 1
        return httpx.Response(200, json={"results": [segment(original_video=original)]})

    assert len(client(handler).search(source, "pedestrians", top_k=6)) == 6
    assert peak == 3


def test_dataset_segments_keep_local_offsets_global_slots_and_original_slot_provenance(video):
    source = excerpt(video)

    def handler(request):
        original = request.url.params["original_video"]
        rows = [
            segment(
                original_video=original,
                source=f"s3://segments/{i}.mp4",
                segment_start_sec=(i - 1) * 10,
                segment_end_sec=i * 10,
                segment_number=i,
            )
            for i in (3, 1, 2)
        ]
        rows.append(segment(original_video="s3://foreign/video.mp4", cosmos_model="foreign-model"))
        return httpx.Response(200, json={"original_video": original, "segments": rows})

    adapter = client(handler)
    rows = adapter.segments(source)
    assert [row["segment_number"] for row in rows] == [1, 2, 3, 4, 5, 6]
    assert [row["source_segment_number"] for row in rows] == [1, 2, 3, 1, 2, 3]
    assert {row["source_total_segments"] for row in rows} == {3}
    assert {row["total_segments"] for row in rows} == {6}
    assert [row["start_seconds"] for row in rows] == [0, 10, 20, 30, 40, 50]
    assert [row["end_seconds"] for row in rows] == [10, 20, 30, 40, 50, 60]
    assert adapter.raw_models == {"nvidia/Cosmos-Reason2-8B"}


def test_dataset_search_rejects_times_that_fit_excerpt_but_exceed_exact_source_chunk(video):
    source = excerpt(video)
    adapter = client(
        lambda request: httpx.Response(
            200,
            json={
                "results": [
                    segment(
                        original_video=json.loads(request.content)["metadata_filters"][
                            "original_video"
                        ],
                        segment_end_sec=31,
                    )
                ]
            },
        )
    )
    with pytest.raises(ProviderError, match="timestamps outside"):
        adapter.search(source, "pedestrians")


def test_dataset_segments_reject_missing_segment_number_with_provider_error(video):
    source = excerpt(video)

    def handler(request):
        original = request.url.params["original_video"]
        row = segment(original_video=original)
        row.pop("segment_number")
        return httpx.Response(200, json={"original_video": original, "segments": [row]})

    with pytest.raises(ProviderError, match="segment"):
        client(handler).segments(source)


@pytest.mark.parametrize("first_slots", [[1, 2, 3, 4], [1, 2, 2, 3]])
def test_dataset_segment_slots_cannot_mask_a_missing_slot_in_another_chunk(video, first_slots):
    source = excerpt(video)

    def handler(request):
        original = request.url.params["original_video"]
        slots = first_slots if original == source.original_video else [2, 3]
        rows = [
            segment(
                original_video=original,
                source=f"s3://segments/{index}.mp4",
                segment_number=number,
                segment_start_sec=0,
                segment_end_sec=30,
            )
            for index, number in enumerate(slots)
        ]
        return httpx.Response(200, json={"original_video": original, "segments": rows})

    with pytest.raises(ProviderError, match="duplicate or out-of-range segment numbers"):
        client(handler).segments(source)


def test_dataset_missing_segment_slot_keeps_recording_indexing_despite_full_time_coverage(video):
    source = excerpt(video)

    def handler(request):
        original = request.url.params["original_video"]
        slots = [1, 2, 3] if original == source.original_video else [2, 3]
        rows = [
            segment(
                original_video=original,
                source=f"s3://segments/{number}.mp4",
                segment_number=number,
                segment_start_sec=0,
                segment_end_sec=30,
            )
            for number in slots
        ]
        return httpx.Response(200, json={"original_video": original, "segments": rows})

    rows = client(handler).segments(source)
    assert [row["segment_number"] for row in rows] == [1, 2, 3, 5, 6]
    assert HighlightService._with_index_status(source, rows).index_status == "indexing"
