"""Corner Scout presents qualified recommendations and clears stale evidence."""

from __future__ import annotations

import html
import importlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def scout_ui(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("corner_scout")


@pytest.fixture
def scout_result(scout_ui):
    ranking = []
    for camera, score, core_frames, frames in [("Camera 3", 0.8, 3, 12), ("Camera 5", 0.5, 2, 9)]:
        corner = {
            "camera": camera,
            "headline": "Market Street",
            "recorded": "1 Oct 2026, 10:04",
            "frames": [{} for _ in range(frames)],
            "video": SimpleNamespace(duration=180),
        }
        seen = dict.fromkeys(scout_ui.TAXONOMY, 0)
        seen["dog"] = core_frames
        ranking.append(
            {
                "corner": corner,
                "score": score,
                "core_frames": core_frames,
                "frames": frames,
                "seen": seen,
            }
        )
    return {
        "business": "Dog grooming salon",
        "profile": SimpleNamespace(customers="Dog owners walking near the shop."),
        "weights": {"dog": 1.0},
        "why": {"dog": "Named in your description"},
        "ranking": ranking,
        "recommend": True,
        "reels": [
            {
                "reel": Path(f"/tmp/corner-scout-test-{index}.mp4"),
                "clips": [
                    {
                        "start": 10,
                        "end": 20,
                        "notable": "A person walks a dog across the street.",
                        "counted": "Dogs ×1",
                        "source": f"original-camera-{index}.mp4 @ 10.0 s",
                    }
                ],
            }
            for index in range(2)
        ],
        "seconds": 4.2,
        "trace_url": None,
    }


def plain_text(markup):
    return html.unescape(re.sub(r"<[^>]+>", " ", markup))


def test_tied_recommendation_names_both_corners(scout_ui, scout_result):
    scout_result["ranking"][1]["score"] = scout_result["ranking"][0]["score"]

    page = plain_text(scout_ui.recommendation_html(scout_result))

    assert "Best corner: Camera 3 and Camera 5" in page


def test_sparse_sample_does_not_announce_a_winner(scout_ui, scout_result):
    scout_result["recommend"] = False
    scout_result["ranking"][0]["core_frames"] = 2

    page = plain_text(scout_ui.recommendation_html(scout_result))

    assert "Not enough evidence to recommend a corner" in page
    assert "2 of 12 sampled frames" in page
    assert "Best corner:" not in page


def test_scores_are_relative_and_use_each_corners_sample_size(scout_ui, scout_result):
    page = plain_text(scout_ui.ranking_html(scout_result))

    assert "Relative match" in page
    assert re.search(r"\b80\s*/100\b", page)
    assert re.search(r"\b50\s*/100\b", page)
    assert "3 of 12 sampled frames" in page
    assert "2 of 9 sampled frames" in page
    assert "not customer probabilities" in page


def test_untrusted_business_profile_location_and_source_are_escaped(scout_ui, scout_result):
    business = '<script>alert("business")</script>'
    customers = '<img src=x onerror="profile()">'
    headline = '<svg onload="location()">Market</svg>'
    source = '<iframe src="https://untrusted.example/">source</iframe>'
    scout_result["business"] = business
    scout_result["profile"].customers = customers
    scout_result["ranking"][0]["corner"]["headline"] = headline
    scout_result["reels"][0]["clips"][0]["source"] = source

    recommendation = scout_ui.recommendation_html(scout_result)
    ranking = scout_ui.ranking_html(scout_result)
    evidence = scout_ui.evidence_html(scout_result, 0)

    for value, page in [
        (business, recommendation),
        (customers, recommendation),
        (headline, ranking),
        (source, evidence),
    ]:
        assert html.escape(value) in page
        assert value not in page


def test_evidence_sources_and_timestamps_start_collapsed(scout_ui, scout_result):
    page = scout_ui.evidence_html(scout_result, 0)
    disclosures = re.findall(r"<details\b([^>]*)>(.*?)</details>", page, flags=re.S)
    source = scout_result["reels"][0]["clips"][0]["source"]

    assert len(disclosures) == 1
    attributes, body = disclosures[0]
    assert not re.search(r"\bopen(?:\s|=|$)", attributes)
    assert "<summary>" in body
    assert source in body
    assert "0:10–0:20" in body


@pytest.mark.parametrize("reels", [[], [None], [{"reel": None, "clips": []}]])
@pytest.mark.parametrize("index", [0, 1])
def test_missing_reels_have_an_explicit_empty_state(scout_ui, scout_result, reels, index):
    scout_result["reels"] = reels

    assert "No clip strong enough" in scout_ui.evidence_html(scout_result, index)


def run_events(app):
    return [event for event in app.fns.values() if getattr(event.fn, "__name__", None) == "run"]


def assert_cleared(update):
    assert len(update) == 9
    assert update[0]["visible"] is True
    assert update[1]["value"] == ""
    assert update[1]["visible"] is False
    assert update[2]["visible"] is False
    assert update[3]["value"] is None
    assert update[3]["visible"] is False
    assert update[4:6] == ("", "")
    assert update[6]["visible"] is False
    assert update[7]["value"] is None
    assert update[7]["visible"] is False
    assert update[8] == ""


def test_all_entrypoints_share_the_generator_without_calls_during_construction(
    scout_ui, scout_result, monkeypatch
):
    calls = []
    monkeypatch.setattr(scout_ui, "scout", lambda *args: calls.append(args))
    app = scout_ui.build_app(None, None, [r["corner"] for r in scout_result["ranking"]])
    events = run_events(app)

    assert calls == []
    assert len(events) == 3  # Example click, primary button and Enter.
    assert all(event.fn is events[0].fn for event in events)
    output_ids = [component._id for component in events[0].outputs]
    assert len(output_ids) == 9
    assert all([component._id for component in event.outputs] == output_ids for event in events)
    assert events[0].outputs[3].interactive is False
    assert events[0].outputs[7].interactive is False


def test_a_new_request_clears_successful_evidence_before_reporting_an_error(
    scout_ui, scout_result, monkeypatch
):
    calls = []

    def successful_scout(*args):
        calls.append(args)
        return scout_result

    monkeypatch.setattr(scout_ui, "scout", successful_scout)
    app = scout_ui.build_app(None, None, [r["corner"] for r in scout_result["ranking"]])
    run = run_events(app)[0].fn
    request = run("Dog grooming salon")

    loading = next(request)
    assert_cleared(loading)
    assert 'role="status"' in loading[0]["value"]
    assert calls == []

    success = next(request)
    assert len(calls) == 1
    assert success[0]["visible"] is False
    assert "Best corner: Camera 3" in success[1]["value"]
    assert success[2]["visible"] is True
    assert success[3]["value"] == str(scout_result["reels"][0]["reel"])
    assert success[3]["visible"] is True
    assert "original-camera-0.mp4" in success[4]
    assert "Relative match" in success[5]
    assert success[6]["visible"] is True
    assert success[7]["value"] == str(scout_result["reels"][1]["reel"])

    error_message = '<script>alert("invalid business")</script>'

    def failed_scout(*_):
        raise ValueError(error_message)

    monkeypatch.setattr(scout_ui, "scout", failed_scout)
    request = run("An invalid business")
    assert_cleared(next(request))
    failure = next(request)

    assert_cleared(failure)
    assert 'role="alert"' in failure[0]["value"]
    assert html.escape(error_message) in failure[0]["value"]
    assert error_message not in failure[0]["value"]
    assert "original-camera" not in str(failure)
    with pytest.raises(StopIteration):
        next(request)


def test_sparse_result_keeps_limited_evidence_and_clears_runner_up(
    scout_ui, scout_result, monkeypatch
):
    scout_result["recommend"] = False
    scout_result["reels"] = scout_result["reels"][:1]
    monkeypatch.setattr(scout_ui, "scout", lambda *_: scout_result)
    app = scout_ui.build_app(None, None, [r["corner"] for r in scout_result["ranking"]])

    _, result = list(run_events(app)[0].fn("Dog grooming salon"))

    assert "Not enough evidence" in result[1]["value"]
    assert result[3]["label"] == "Limited evidence · Camera 3"
    assert result[3]["visible"] is True
    assert result[6]["visible"] is False
    assert result[7]["value"] is None
    assert result[7]["visible"] is False
    assert result[8] == ""


@pytest.mark.parametrize(
    ("failure", "shown"),
    [
        ("render", "The rendered duration does not match the selected intervals."),
        ("unexpected", "Something went wrong while preparing the evidence."),
    ],
)
def test_any_failure_replaces_the_loading_view(scout_ui, scout_result, monkeypatch, failure, shown):
    from highlight_agent.media import MediaError

    def failing_scout(*_):
        if failure == "render":
            raise MediaError("The rendered duration does not match the selected intervals.")
        raise KeyError("internal detail")

    monkeypatch.setattr(scout_ui, "scout", failing_scout)
    app = scout_ui.build_app(None, None, [r["corner"] for r in scout_result["ranking"]])

    loading, result = list(run_events(app)[0].fn("Dog grooming salon"))

    assert 'role="status"' in loading[0]["value"]
    assert_cleared(result)
    assert 'role="alert"' in result[0]["value"]
    assert shown in result[0]["value"]
    assert "internal detail" not in result[0]["value"]


def test_runner_up_panel_needs_a_clip_to_show(scout_ui, scout_result, monkeypatch):
    scout_result["reels"][1] = None
    monkeypatch.setattr(scout_ui, "scout", lambda *_: scout_result)
    app = scout_ui.build_app(None, None, [r["corner"] for r in scout_result["ranking"]])

    _, result = list(run_events(app)[0].fn("Dog grooming salon"))

    assert "Best corner: Camera 3" in result[1]["value"]
    assert result[6]["visible"] is False
    assert result[7]["visible"] is False
    assert result[8] == ""


def test_empty_evidence_names_its_corner(scout_ui, scout_result):
    scout_result["reels"] = []

    page = scout_ui.evidence_html(scout_result, 1)

    assert "No clip strong enough at Camera 5" in page
    assert "Try another business" not in page


def test_every_entrypoint_hides_gradio_progress(scout_ui, scout_result, monkeypatch):
    monkeypatch.setattr(scout_ui, "scout", lambda *_: scout_result)
    app = scout_ui.build_app(None, None, [r["corner"] for r in scout_result["ranking"]])

    assert [event.show_progress for event in run_events(app)] == ["hidden"] * 3


def test_dark_mode_keeps_video_labels_readable(scout_ui):
    theme = scout_ui.create_theme()

    for name in ["block_label_text_color", "block_label_background_fill", "block_title_text_color"]:
        assert getattr(theme, f"{name}_dark") == getattr(theme, name)
