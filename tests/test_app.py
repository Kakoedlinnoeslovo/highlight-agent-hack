"""UI provenance, dataset selection, and saved timing behavior."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import PRO_BRIEF, ROAST_BRIEF, _request_seconds, build_app
from highlight_agent.briefs import STREET_PEOPLE_BRIEF, STREET_REVISION, STREET_TRAFFIC_BRIEF


@pytest.mark.parametrize(
    ("timings", "expected"),
    [
        ({"total": 15.0, "render": 12.34}, 15.0),
        ({"total_seconds": 15.0}, 15.0),
        ({"render": 12.34}, 12.34),
        ({"render": 0.0}, 0.0),
        ({}, None),
    ],
)
def test_restored_edit_uses_saved_timing_or_reports_it_unavailable(timings, expected):
    assert _request_seconds(timings, 0.001, restored=True) == expected


def test_current_revision_uses_actual_request_elapsed_when_only_render_was_saved():
    assert _request_seconds({"render": 12.34}, 15.0, restored=False) == 15.0


class DatasetService:
    def __init__(self, *, mode="live", include_dataset=True):
        self.mode = mode
        self.calls = []
        self.records = [
            {
                "id": "legacy",
                "label": "Earlier gameplay",
                "local_path": "gameplay.mp4",
                "duration": 60,
            },
        ]
        self.city = {
            "id": "street",
            "label": "SF Streets · camera 5",
            "local_path": "street.mp4",
            "duration": 180,
            "dataset": "sf-streets",
            "index_status": "ready",
        }
        if include_dataset:
            self.records.append(self.city)

    def status(self):
        return {"mode": self.mode}

    def list_videos(self):
        return self.records

    def dataset_sources(self):
        self.calls.append(("browse",))
        return [
            {
                "original_video": "https://dataset.example/cam5/001.mp4",
                "label": self.city["label"],
                "segment_count": 6,
            }
        ]

    def import_dataset_source(self, original_video):
        self.calls.append(("import", original_video))
        self.records.append(self.city)
        return self.city


def component(app, label):
    return next(item for item in app.blocks.values() if getattr(item, "label", None) == label)


def callback(app, name):
    return next(item.fn for item in app.fns.values() if item.fn.__name__ == name)


def test_live_construction_uses_only_supplied_dataset_without_provider_calls():
    service = DatasetService()
    app = build_app(service)

    sources = component(app, "Source recording")
    assert sources.choices == [(service.city["label"], "street")]
    assert sources.value == "street"
    assert component(app, "Original recording").value["path"].endswith("street.mp4")
    assert service.calls == []
    assert not any(getattr(item, "label", None) == "Video file" for item in app.blocks.values())
    assert component(app, "Revision").value == STREET_REVISION
    button_values = [
        getattr(item, "value", None)
        for item in app.blocks.values()
        if item.__class__.__name__ == "Button"
    ]
    assert STREET_PEOPLE_BRIEF + "  ↗" in button_values
    assert STREET_TRAFFIC_BRIEF + "  ↗" in button_values


def test_live_dataset_browse_import_and_source_change_update_existing_editor():
    service = DatasetService(include_dataset=False)
    app = build_app(service)
    assert component(app, "Source recording").value is None
    assert component(app, "Source recording").choices == []

    choices, action, notice = callback(app, "browse_dataset")()
    original = "https://dataset.example/cam5/001.mp4"
    assert choices["choices"] == [(service.city["label"] + " · 6 indexed segments", original)]
    assert choices["value"] == original
    assert action["interactive"] is True
    assert "Found 1 hackathon recordings" in notice

    sources, metadata, notice = callback(app, "import_dataset_source")(original)
    assert sources["choices"] == [(service.city["label"], "street")]
    assert sources["value"] == "street"
    assert "READY" in metadata
    assert "footage ready" in notice
    assert service.calls == [("browse",), ("import", original)]

    changed = callback(app, "choose_source")(
        "street",
        {
            "video_id": "legacy",
            "edit_id": "old",
            "windows": [{"start_seconds": 1, "end_seconds": 5}],
        },
    )
    assert changed[2]["value"] == "street.mp4"
    assert changed[3] == {"edit_id": None, "video_id": "street", "windows": []}
    assert changed[8] == []  # Clear the previous cut's evidence rows.


def test_offline_sample_preserves_existing_fixture_sources_and_directions():
    app = build_app(DatasetService(mode="offline_sample", include_dataset=False))
    assert component(app, "Source recording").choices == [("Earlier gameplay", "legacy")]
    assert component(app, "Video file") is not None
    button_values = [
        getattr(item, "value", None)
        for item in app.blocks.values()
        if item.__class__.__name__ == "Button"
    ]
    assert PRO_BRIEF + "  ↗" in button_values
    assert ROAST_BRIEF + "  ↗" in button_values


class EditSessionService(DatasetService):
    def __init__(self, result_video="street"):
        super().__init__()
        self.result_video = result_video

    def get_result(self, edit_id):
        self.calls.append(("read", edit_id))
        return {
            "edit_plan": {
                "id": edit_id,
                "video_id": self.result_video,
                "source_duration": 180,
                "windows": [
                    {
                        "start_seconds": 1,
                        "end_seconds": 4,
                        "moment_ids": ["m"],
                        "caption": "A pedestrian crosses.",
                    }
                ],
            },
            "output_path": "cut.mp4",
            "mode": "live",
        }

    def revise_edit(self, edit_id, instruction, progress=None):
        self.calls.append(("revise", edit_id))
        return self.get_result("revised")

    def undo_edit(self, edit_id):
        self.calls.append(("undo", edit_id))
        return self.get_result("previous")

    def preview_moment(self, edit_id, index):
        self.calls.append(("preview", edit_id))
        return Path("preview.mp4")


def session_action(app, action, session):
    if action == "preview":
        return callback(app, action)(session, SimpleNamespace(index=0))[1]
    values = list(
        callback(app, action)("Remove clip two", session)
        if action == "revise"
        else callback(app, action)(session)
    )
    return values[-1][7]


@pytest.mark.parametrize("action", ["revise", "undo", "preview"])
@pytest.mark.parametrize(
    ("session_video", "result_video"), [("legacy", "legacy"), ("street", "legacy")]
)
def test_live_edit_actions_reject_stale_or_mismatched_legacy_sessions(
    action, session_video, result_video
):
    service = EditSessionService(result_video)
    app = build_app(service)
    notice = session_action(
        app, action, {"video_id": session_video, "edit_id": "old-edit", "windows": []}
    )

    assert "ha-stage error" in notice
    assert not any(call[0] in {"revise", "undo", "preview"} for call in service.calls)


@pytest.mark.parametrize("action", ["revise", "undo", "preview"])
def test_live_edit_actions_allow_edits_belonging_to_current_dataset_source(action):
    service = EditSessionService()
    app = build_app(service)
    session_action(app, action, {"video_id": "street", "edit_id": "street-edit", "windows": []})
    assert (action, "street-edit") in service.calls


def test_source_change_clears_stale_edit_even_when_invalid_video_id_has_not_changed():
    app = build_app(DatasetService())
    values = callback(app, "choose_source")(
        "legacy",
        {
            "video_id": "legacy",
            "edit_id": "old-edit",
            "windows": [{"start_seconds": 1, "end_seconds": 5}],
        },
    )
    assert values[2]["value"] is None
    assert values[3]["edit_id"] is None
    assert values[3]["windows"] == []


def full_recording(index=0, *, stream_id=None, chunks=None):
    chunks = chunks or [{"original_video": f"s3://supplied/session-{index}/chunk-0.mp4"}]
    return {"id": f"full-{index}", "label": f"Toronto · camera 1 · session {index}",
            "local_path": f"full-{index}.mp4", "duration": 600,
            "dataset": "hackathon-public", "index_status": "ready",
            "stream_id": stream_id or f"session-{index}", "camera_id": "toronto-cam-1",
            "location": "toronto", "capture_type": "streets",
            "original_video": chunks[0]["original_video"], "source_chunks": chunks}


def test_live_editor_lists_all_full_recordings_without_duplicate_excerpts_or_personal_sources():
    service = DatasetService()
    full = [full_recording(index) for index in range(55)]
    service.city["original_video"] = full[0]["original_video"]
    service.records += full

    app = build_app(service)

    choices = component(app, "Source recording").choices
    assert len(choices) == 55
    assert {value for _, value in choices} == {item["id"] for item in full}
    assert service.calls == []
    chosen = callback(app, "choose_source")("full-54", {"video_id": None, "windows": []})
    assert chosen[2]["value"] == "full-54.mp4"
    assert chosen[3]["video_id"] == "full-54"
    assert chosen[3]["edit_id"] is None


def test_current_inventory_excludes_superseded_full_membership_and_its_legacy_excerpt(tmp_path):
    from highlight_agent.service import HighlightService
    from highlight_agent.storage import write_json

    service = DatasetService()
    current_source = {"stream_id": "same-session", "camera_id": "toronto-cam-1",
                      "location": "toronto", "capture_type": "streets",
                      "chunks": [{"original_video": f"s3://supplied/same-session/chunk-{index}.mp4",
                                  "duration": 30, "chunk_index": index, "segment_count": 6}
                                 for index in range(2)]}
    current = full_recording(1, stream_id="same-session", chunks=current_source["chunks"])
    current["import_signature"] = HighlightService.dataset_import_signature(current_source)
    previous_source = {**current_source, "chunks": current_source["chunks"][:1]}
    previous = full_recording(0, stream_id="same-session", chunks=previous_source["chunks"])
    previous["import_signature"] = HighlightService.dataset_import_signature(previous_source)
    service.city["original_video"] = current["original_video"]
    service.records += [previous, current]
    service.config = SimpleNamespace(data_dir=tmp_path / "data")
    write_json(service.config.data_dir / "catalog/inventory.json", {"sources": [current_source]})

    app = build_app(service)

    assert component(app, "Source recording").choices == [(current["label"], current["id"])]
    assert service.calls == []
    stale = callback(app, "choose_source")(previous["id"], {"video_id": previous["id"],
                                                           "edit_id": "old", "windows": []})
    assert stale[2]["value"] is None and stale[3]["edit_id"] is None


def test_full_collection_source_can_make_a_cut_in_the_existing_editor():
    class FullCollectionService(EditSessionService):
        def __init__(self):
            super().__init__(result_video="full-0")
            self.records = [self.records[0], full_recording()]

        def make_highlight(self, video_id, prompt, max_duration, progress):
            self.calls.append(("make", video_id, prompt, max_duration))
            progress("Searching supplied captions")
            return self.get_result("new-full-cut")

    service = FullCollectionService()
    app = build_app(service)
    assert service.calls == []

    updates = list(callback(app, "make_cut")("full-0", "Find passing bicycles", 30,
                                           {"video_id": "full-0", "windows": []}))

    assert ("make", "full-0", "Find passing bicycles", 30.0) in service.calls
    assert updates[-1][0]["video_id"] == "full-0"
    assert "Your cut is ready" in updates[-1][7]
