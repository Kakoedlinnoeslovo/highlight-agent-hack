"""Service boundaries with injected providers/renderer; no API keys or network are used."""

import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from highlight_agent.config import Settings
from highlight_agent.inference import SearchBrief
from highlight_agent.media import MediaInfo
from highlight_agent.models import Moment
from highlight_agent.selection import SelectionError
from highlight_agent.service import HighlightService, ServiceError
from highlight_agent.storage import read_json, write_json
from highlight_agent.tracing import Tracer


class FakeVAST:
    def __init__(self):
        self.uploads = 0
        self.segment_calls = 0
        self.search_calls = []
        self.search_limit = None
        self.empty = False
        self.rows = [
            {
                "start_seconds": i * 20,
                "end_seconds": (i + 1) * 20,
                "caption": "Indexed visible vehicle motion.",
                "segment_number": i + 1,
                "total_segments": 6,
                "cosmos_model": "actual-caption-model",
            }
            for i in range(6)
        ]

    def upload(self, video):
        self.uploads += 1
        return "s3://test-bucket/exact-recording.mp4"

    def segments(self, video):
        self.segment_calls += 1
        return self.rows

    def search(self, video, query):
        self.search_calls.append((video.id, video.original_video, query))
        if self.empty:
            return []
        offset = 5 if "clean" in query else 25
        moments = [
            Moment(
                id=f"{offset}-{index}",
                video_id=video.id,
                start_seconds=offset + index * 40,
                end_seconds=offset + index * 40 + 2,
                caption="Vehicle event with visible aftermath.",
                score=8 + index * 0.4,
            )
            for index in range(3)
        ]
        return moments[: self.search_limit] if self.search_limit is not None else moments


class FakeInference:
    def __init__(self):
        self.expanded = []
        self.ranked = []

    def expand(self, prompt):
        self.expanded.append(prompt)
        category = "clean landings" if "pro" in prompt else "crashes"
        return SearchBrief(query=category, alternate_query=f"alternate {category}")

    def rank(self, prompt, candidates):
        self.ranked.append([moment.id for moment in candidates])
        return candidates


class FakeRenderer:
    def __init__(self):
        self.plans = []

    def __call__(self, plan, directory):
        self.plans.append(plan)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "reel.mp4"
        path.write_bytes(b"injected render result")
        return path


def config(tmp_path, **kwargs):
    values = dict(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "outputs",
        vast_base_url="https://test.example",
        vast_access_token="test-only-token",
        wandb_api_key="test-only-key",
        wandb_model="test-model",
        wandb_project="test-team/test-project",
        vast_caption_model="configured-only",
    )
    values.update(kwargs)
    return Settings(**values)


@pytest.fixture
def service(tmp_path, monkeypatch):
    # Explicitly prevent a remote Weave client even though service configuration is complete.
    monkeypatch.setattr(Tracer, "initialize", lambda self: setattr(self, "initialized", True))
    monkeypatch.setattr(
        "highlight_agent.service.probe_media", lambda path: MediaInfo(120, 640, 360, False)
    )
    vast, inference, renderer = FakeVAST(), FakeInference(), FakeRenderer()
    instance = HighlightService(
        config(tmp_path), vast_client=vast, inference_client=inference, renderer=renderer
    )
    return instance, vast, inference, renderer


def register(service, tmp_path):
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"exact source recording")
    return service.register_video(source, "Driving session")


def ready(service, tmp_path):
    video = register(service, tmp_path)
    service.ingest_video(video.id)
    return service.refresh_index(video.id)


def test_one_index_supports_contrasting_prompts_and_persists_auditable_outputs(service, tmp_path):
    app, vast, inference, renderer = service
    video = ready(app, tmp_path)
    assert video.index_status == "ready" and video.caption_model == "actual-caption-model"
    progress = []
    pro = app.make_highlight(video.id, "Make me look like a pro", progress=progress.append)
    roast = app.make_highlight(video.id, "Now roast my driving")
    assert vast.uploads == 1 and vast.segment_calls == 1
    assert len(vast.search_calls) == 2 and len(renderer.plans) == 2
    assert {call[1] for call in vast.search_calls} == {video.original_video}
    assert {m.id for m in pro.moments}.isdisjoint(m.id for m in roast.moments)
    assert pro.trace_url is None and roast.trace_url is None
    assert pro.mode == roast.mode == "live"
    assert pro.output_path.exists() and pro.edit_plan.duration <= 30
    assert {"translate_brief", "search_1", "rank_1", "render", "total"} <= pro.timings.keys()
    assert progress[-1].startswith("Ready")
    restored = app.get_result(pro.edit_plan.id)
    assert restored == pro
    evidence = read_json(pro.output_path.parent / "evidence.json")
    assert evidence["reported_caption_model"] == "actual-caption-model"
    assert evidence["queries"] == ["clean landings"]
    assert "test-only-key" not in str(evidence) and "test-only-token" not in str(evidence)
    assert any("not freshly rewatched" in warning for warning in pro.warnings)


def test_one_alternate_query_is_bounded_and_duplicate_hits_are_not_repeated(service, tmp_path):
    app, vast, inference, _ = service
    video = ready(app, tmp_path)
    vast.search_limit = 2
    result = app.make_highlight(video.id, "Make me look like a pro")
    assert len(vast.search_calls) == 2
    assert len(result.moments) == 2
    assert len(inference.ranked) == 2 and all(len(ids) == 2 for ids in inference.ranked)
    assert len({identifier for w in result.edit_plan.windows for identifier in w.moment_ids}) == 2


@pytest.mark.parametrize("alternate_fits", [True, False])
def test_alternate_search_runs_when_three_ranked_hits_cannot_fit_the_duration(
    service, tmp_path, monkeypatch, alternate_fits
):
    app, vast, inference, renderer = service
    video = ready(app, tmp_path)
    oversized = [
        Moment(
            id=f"long-{index}",
            video_id=video.id,
            start_seconds=start,
            end_seconds=start + 31,
            caption="Complete visible event with aftermath.",
            score=9,
        )
        for index, start in enumerate([0, 40, 80])
    ]
    fitting = Moment(
        id="short-alternate",
        video_id=video.id,
        start_seconds=90,
        end_seconds=92,
        caption="A short complete crash with visible aftermath.",
        score=8,
    )

    def search(source, query):
        vast.search_calls.append((source.id, source.original_video, query))
        return [fitting] if query.startswith("alternate") and alternate_fits else oversized

    monkeypatch.setattr(vast, "search", search)
    if alternate_fits:
        result = app.make_highlight(video.id, "Now roast my driving")
        assert [item.id for item in result.moments] == [fitting.id]
        assert result.edit_plan.duration == 7
        assert len(renderer.plans) == 1
    else:
        with pytest.raises(SelectionError, match="No complete"):
            app.make_highlight(video.id, "Now roast my driving")
        assert renderer.plans == []
    assert len(vast.search_calls) == 2
    assert len(inference.ranked) == 2


def test_no_matches_never_invokes_renderer_or_creates_a_pretend_result(service, tmp_path):
    app, vast, _, renderer = service
    video = ready(app, tmp_path)
    vast.empty = True
    with pytest.raises(SelectionError, match="No caption-supported"):
        app.make_highlight(video.id, "Now roast my driving")
    assert len(vast.search_calls) == 2 and renderer.plans == []
    assert not list(app.config.output_dir.glob("*/result.json"))


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([(0, 60, 1, 2), (60, 120, 2, 2)], "ready"),
        ([(0, 60, 1, 2)], "indexing"),
        ([(0, 40, 1, 2), (42, 120, 2, 2)], "indexing"),
        ([(0, 60, 1, 3), (60, 120, 2, 3)], "indexing"),
        ([(0, 60, 1, 2), (60, 120, 2, 3)], "indexing"),
        ([(0, 60, 1, 2), (60, 120, 1, 2)], "indexing"),
        ([(0, 60, None, None), (60, 120, None, None)], "ready"),
        ([], "indexing"),
    ],
)
def test_readiness_requires_full_coverage_and_consistent_declared_segments(
    service, tmp_path, rows, expected
):
    app, vast, _, _ = service
    video = register(app, tmp_path)
    app.ingest_video(video.id)
    vast.rows = [
        dict(
            start_seconds=start,
            end_seconds=end,
            segment_number=number,
            total_segments=total,
            caption="Actual indexed caption",
        )
        for start, end, number, total in rows
    ]
    refreshed = app.refresh_index(video.id)
    assert refreshed.index_status == expected
    assert refreshed.caption_model is None  # Configured model hint is not executed-model evidence.
    if expected != "ready":
        with pytest.raises(ServiceError, match="not fully indexed"):
            app.make_highlight(video.id, "Make me look like a pro")


def test_revisions_preserve_windows_original_artifacts_and_undo(service, tmp_path):
    app, _, _, _ = service
    original = app.make_highlight(ready(app, tmp_path).id, "Now roast my driving")
    original_json = (original.output_path.parent / "result.json").read_bytes()
    revised = app.revise_edit(
        original.edit_plan.id, "Remove clip two and put the highest-ranked crash first."
    )
    assert revised.edit_plan.previous_edit_id == original.edit_plan.id
    assert len(revised.edit_plan.windows) == 2 and revised.edit_plan.windows[0].score == 8.8
    assert app.undo_edit(revised.edit_plan.id) == original
    assert (original.output_path.parent / "result.json").read_bytes() == original_json
    with pytest.raises(ServiceError, match="no earlier revision"):
        app.undo_edit(original.edit_plan.id)
    with pytest.raises(SelectionError, match="Supported revisions"):
        app.revise_edit(original.edit_plan.id, "Use a different source recording")


def test_source_change_blocks_new_generation_and_revision(service, tmp_path):
    app, _, _, renderer = service
    video = ready(app, tmp_path)
    original = app.make_highlight(video.id, "Now roast my driving")
    video.local_path.write_bytes(b"replacement footage")
    with pytest.raises(ServiceError, match="source file changed"):
        app.make_highlight(video.id, "Make me look like a pro")
    with pytest.raises(ServiceError, match="source file changed"):
        app.revise_edit(original.edit_plan.id, "Remove clip two")
    assert len(renderer.plans) == 1


@pytest.mark.parametrize("missing", [False, True])
def test_changed_or_missing_registered_source_never_reaches_upload(service, tmp_path, missing):
    app, vast, _, _ = service
    video = register(app, tmp_path)
    if missing:
        video.local_path.unlink()
    else:
        video.local_path.write_bytes(b"replacement footage")
    with pytest.raises(
        ServiceError, match="source is missing" if missing else "source file changed"
    ):
        app.ingest_video(video.id)
    assert vast.uploads == 0
    assert app.get_video(video.id).original_video is None


@pytest.mark.parametrize("missing", [False, True])
def test_reregistering_exact_source_restores_media_and_preserves_ready_index(
    service, tmp_path, missing
):
    app, vast, _, renderer = service
    video = ready(app, tmp_path)
    record_before = app._video_path(video.id).read_bytes()
    index_before = app._index_path(video.id).read_bytes()
    if missing:
        video.local_path.unlink()
    else:
        video.local_path.write_bytes(b"replacement footage")

    restored = app.register_video(tmp_path / "recording.mp4", original_video=video.original_video)

    assert restored == video
    assert restored.local_path.read_bytes() == b"exact source recording"
    assert app._video_path(video.id).read_bytes() == record_before
    assert app._index_path(video.id).read_bytes() == index_before
    assert len(app.list_videos()) == 1
    result = app.make_highlight(video.id, "Now roast my driving")
    assert result.output_path.is_file() and len(renderer.plans) == 1
    assert vast.uploads == 1
    assert not list(video.local_path.parent.glob(".restore-*"))


def test_failed_reregistration_does_not_install_media_that_changed_during_copy(
    service, tmp_path, monkeypatch
):
    app, _, _, _ = service
    video = register(app, tmp_path)
    video.local_path.write_bytes(b"previous stored copy")
    monkeypatch.setattr(
        "highlight_agent.service.shutil.copyfile",
        lambda source, destination: destination.write_bytes(b"changed during copy"),
    )
    with pytest.raises(ServiceError, match="source file changed while restoring"):
        app.register_video(tmp_path / "recording.mp4")
    assert video.local_path.read_bytes() == b"previous stored copy"
    assert not list(video.local_path.parent.glob(".restore-*"))


def test_missing_live_configuration_is_explicit_and_does_not_fall_back(service, tmp_path):
    app, _, _, renderer = service
    video = ready(app, tmp_path)
    missing = HighlightService(
        replace(app.config, wandb_api_key=""),
        vast_client=FakeVAST(),
        inference_client=FakeInference(),
        renderer=renderer,
    )
    with pytest.raises(ServiceError, match="Missing configuration: WANDB_API_KEY"):
        missing.make_highlight(video.id, "Make me look like a pro")
    assert renderer.plans == []


def test_fixture_and_live_media_edits_remain_isolated(service, tmp_path):
    app, _, _, renderer = service
    video = ready(app, tmp_path)
    result = app.make_highlight(video.id, "Now roast my driving")
    offline = HighlightService(replace(app.config, mode="offline_sample"), renderer=renderer)
    assert offline.list_videos() == []
    with pytest.raises(ServiceError, match="different mode"):
        offline.get_video(video.id)
    with pytest.raises(ServiceError, match="different mode"):
        offline.get_result(result.edit_plan.id)
    with pytest.raises(ServiceError, match="explicitly"):
        app.register_sample()
    with pytest.raises(ServiceError, match="Invalid video or edit ID"):
        app.get_video("../escape")


def test_offline_mode_never_initializes_or_calls_sponsor_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "highlight_agent.service.probe_media", lambda path: MediaInfo(120, 640, 360, False)
    )
    calls = []
    fake_weave = SimpleNamespace(init=lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setitem(sys.modules, "weave", fake_weave)
    renderer = FakeRenderer()
    app = HighlightService(config(tmp_path, mode="offline_sample"), renderer=renderer)
    video = register(app, tmp_path)
    app._save_video(video.model_copy(update={"index_status": "ready"}))
    write_json(
        app._index_path(video.id),
        [
            Moment(
                id="manual",
                video_id=video.id,
                start_seconds=5,
                end_seconds=7,
                caption="Synthetic fixture, not gameplay",
                category="crash",
                evidence_type="manual",
                score=8,
            ).model_dump(mode="json")
        ],
    )
    result = app.make_highlight(video.id, "Now roast my driving")
    assert calls == [] and app._vast is None and app._inference is None
    assert result.trace_url is None and result.mode == "offline_sample"
    assert result.moments[0].evidence_type == "manual"
    assert any("SYNTHETIC SAMPLE" in warning for warning in result.warnings)
    with pytest.raises(ServiceError, match="never uploads"):
        app.ingest_video(video.id)


def test_failed_remote_trace_creation_does_not_invent_url_or_block_local_work(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("not remotely connected")

    tracer = Tracer(Settings(mode="live", wandb_api_key="test-key", wandb_project="team/project"))
    monkeypatch.setitem(
        sys.modules,
        "weave",
        SimpleNamespace(init=lambda project, **kwargs: SimpleNamespace(create_call=fail)),
    )
    tracer.initialize()
    with tracer.span("highlight.make", {"prompt": "crashes"}) as span:
        span["output"] = {"complete": True}
        assert span["url"] is None
    assert "creation failed" in tracer.warning


def test_broken_trace_url_does_not_block_successful_edit_work(monkeypatch):
    class BrokenCall:
        @property
        def ui_url(self):
            raise RuntimeError("trace URL unavailable")

    finished = []
    remote = SimpleNamespace(
        create_call=lambda *args, **kwargs: BrokenCall(),
        finish_call=lambda *args, **kwargs: finished.append(kwargs),
    )
    tracer = Tracer(Settings(mode="live", wandb_api_key="test-key", wandb_project="team/project"))
    monkeypatch.setitem(
        sys.modules, "weave", SimpleNamespace(init=lambda project, **kwargs: remote)
    )
    tracer.initialize()
    with tracer.span("highlight.make", {"prompt": "crashes"}) as span:
        span["output"] = {"complete": True}
        assert span["url"] is None
    assert finished and tracer.warning


def test_changed_or_missing_source_cannot_be_used_for_evidence_preview(
    service, tmp_path, monkeypatch
):
    app, _, _, _ = service
    video = ready(app, tmp_path)
    original = app.make_highlight(video.id, "Now roast my driving")
    previews = []
    monkeypatch.setattr(
        "highlight_agent.service.preview_window", lambda *args: previews.append(args)
    )
    video.local_path.write_bytes(b"replacement footage")
    with pytest.raises(ServiceError, match="source file changed"):
        app.preview_moment(original.edit_plan.id, 0)
    video.local_path.unlink()
    with pytest.raises(ServiceError, match="source is missing"):
        app.make_highlight(video.id, "Make me look like a pro")
    assert previews == []


def test_revision_reports_its_own_trace_failure_without_mutating_previous_warnings(
    service, tmp_path
):
    app, _, _, _ = service
    original = app.make_highlight(ready(app, tmp_path).id, "Now roast my driving")
    previous_warnings = original.warnings.copy()

    def fail(*args, **kwargs):
        raise RuntimeError("revision trace unavailable")

    app.tracer.client = SimpleNamespace(create_call=fail)
    revised = app.revise_edit(original.edit_plan.id, "Remove clip two")
    assert revised.trace_url is None
    assert any("trace creation failed" in warning for warning in revised.warnings)
    assert app.get_result(original.edit_plan.id).warnings == previous_warnings
