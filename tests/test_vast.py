"""Validate hosted VSS contracts with actual HTTP request serialization."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from highlight_agent.config import Settings
from highlight_agent.models import Moment, VideoRecord
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

    assert client(handler, vast_upload_bucket="uploads").upload(video, caption_prompt=prompt) == ORIGINAL


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
