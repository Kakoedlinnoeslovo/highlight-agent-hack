"""Editing invariants: complete contextual windows, source bounds, and safe revisions."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from highlight_agent.models import EditPlan, EditWindow, Moment, VideoRecord
from highlight_agent.selection import SelectionError, create_plan, revise_plan, select_windows


def video(duration=100):
    return VideoRecord(id="v1", label="Driving", local_path=Path("source.mp4"), duration=duration)


def moment(identifier, start, end, *, score=8, video_id="v1"):
    return Moment(
        id=identifier,
        video_id=video_id,
        start_seconds=start,
        end_seconds=end,
        caption=f"Visible event {identifier}",
        score=score,
        reason="Indexed caption",
    )


def test_padding_merges_event_boundary_hits_deduplicates_and_clamps_endpoints():
    windows = select_windows(
        video(30),
        [
            moment("a", 0.5, 2, score=8),
            moment("a", 0.5, 2, score=8),
            moment("b", 4, 5, score=9),
            moment("c", 28, 30, score=7),
        ],
        30,
    )
    assert len(windows) == 2
    assert [(w.start_seconds, w.end_seconds) for w in windows] == [(26, 30), (0, 8)]
    assert windows[1].moment_ids == ["a", "b"]
    assert windows[-1].score == 9


def test_duration_budget_keeps_complete_events_without_truncation():
    candidates = [
        moment("a", 10, 15, score=10),
        moment("b", 30, 35, score=9),
        moment("c", 60, 65, score=8),
    ]
    windows = select_windows(video(), candidates, 15)
    assert len(windows) == 1
    assert windows[0].moment_ids == ["a"]
    assert (windows[0].start_seconds, windows[0].end_seconds) == (8, 18)
    with pytest.raises(SelectionError, match="No complete"):
        select_windows(video(), candidates, 9)


@pytest.mark.parametrize("strongest_first", [True, False])
def test_overlapping_padding_keeps_the_highest_ranked_complete_event(strongest_first):
    candidates = [
        moment("first", 10, 30, score=9 if strongest_first else 8),
        moment("second", 31, 51, score=8 if strongest_first else 9),
    ]
    windows = select_windows(video(), candidates, 30)
    assert len(windows) == 1
    if strongest_first:
        assert windows[0].moment_ids == ["first"]
        assert (windows[0].start_seconds, windows[0].end_seconds) == (8, 33)
    else:
        assert windows[0].moment_ids == ["second"]
        assert (windows[0].start_seconds, windows[0].end_seconds) == (29, 54)


def test_overlapping_chain_keeps_a_complete_subset_when_the_full_union_cannot_fit():
    candidates = [moment(f"m{i}", 10 + i * 8, 15 + i * 8, score=7 + i) for i in range(4)]
    plan = create_plan(video(), "crashes", candidates)
    assert len(plan.windows) == 1
    assert plan.windows[0].moment_ids == ["m1", "m2", "m3"]
    assert (plan.windows[0].start_seconds, plan.windows[0].end_seconds) == (16, 42)
    assert plan.duration == 26
    assert plan.windows[0].score == 10


def test_oversized_event_does_not_hide_a_complete_fitting_neighbor():
    windows = select_windows(
        video(),
        [moment("oversized", 10, 50, score=10), moment("fitting", 20, 25, score=9)],
        30,
    )
    assert len(windows) == 1 and windows[0].moment_ids == ["fitting"]
    assert (windows[0].start_seconds, windows[0].end_seconds) == (18, 28)


def test_admitted_bridge_merges_both_neighbor_windows_without_duplicate_footage():
    plan = create_plan(
        video(),
        "crashes",
        [
            moment("first", 10, 11, score=10),
            moment("last", 20, 21, score=9),
            moment("bridge", 15, 16, score=8),
        ],
    )
    assert len(plan.windows) == 1
    assert plan.windows[0].moment_ids == ["first", "bridge", "last"]
    assert (plan.windows[0].start_seconds, plan.windows[0].end_seconds) == (8, 24)


def test_selection_limits_four_events_and_ends_with_strongest():
    candidates = [moment(f"m{i}", 3 + 15 * i, 4 + 15 * i, score=6 + i * 0.5) for i in range(6)]
    windows = select_windows(video(), candidates, 30)
    assert len(windows) == 4
    assert sum(w.duration for w in windows) == 24
    assert windows[-1].moment_ids == ["m5"]
    assert {identifier for w in windows for identifier in w.moment_ids} == {"m2", "m3", "m4", "m5"}


def street_plan():
    source = video(30).model_copy(update={"dataset": "sf-streets"})
    candidates = [moment(f"m{i}", i * 5, (i + 1) * 5, score=10 - i * 0.2) for i in range(6)]
    return create_plan(source, "Show me pedestrians and cyclists.", candidates, 30)


def test_dense_street_captions_keep_separate_complete_contextual_windows():
    plan = street_plan()
    assert [window.moment_ids for window in plan.windows] == [["m4"], ["m2"], ["m0"]]
    assert [(window.start_seconds, window.end_seconds) for window in plan.windows] == [
        (18, 28),
        (8, 18),
        (0, 8),
    ]
    assert plan.duration == 28
    chronological = sorted(plan.windows, key=lambda window: window.start_seconds)
    assert all(
        left.end_seconds <= right.start_seconds
        for left, right in zip(chronological, chronological[1:])
    )
    assert all(" · " not in window.caption for window in plan.windows)


def test_street_overlapping_padded_caption_yields_to_higher_ranked_complete_moment():
    source = video().model_copy(update={"dataset": "sf-streets"})
    windows = select_windows(
        source,
        [
            moment("lower", 10, 15, score=7),
            moment("higher", 14, 19, score=10),
            moment("separate", 30, 35, score=8),
        ],
        30,
    )
    assert [window.moment_ids for window in windows] == [["separate"], ["higher"]]
    assert [(window.start_seconds, window.end_seconds) for window in windows] == [
        (28, 38),
        (12, 22),
    ]


def test_dense_street_cut_can_remove_clip_two_and_move_highest_ranked_moment_first():
    plan = street_plan()
    original = plan.model_dump()
    revised = revise_plan(plan, "Remove clip two and put the highest-ranked moment first.")
    assert [window.moment_ids for window in revised.windows] == [["m0"], ["m4"]]
    assert [(window.start_seconds, window.end_seconds) for window in revised.windows] == [
        (0, 8),
        (18, 28),
    ]
    assert revised.previous_edit_id == plan.id
    assert plan.model_dump() == original


@pytest.mark.parametrize(
    "candidate", [moment("wrong-source", 5, 6, video_id="v2"), moment("past-end", 99, 101)]
)
def test_foreign_and_out_of_source_candidates_are_rejected(candidate):
    with pytest.raises(SelectionError, match="registered source"):
        select_windows(video(), [candidate], 30)


@pytest.mark.parametrize("duration", [0, -1, 30.1, float("nan"), float("inf")])
def test_non_finite_or_out_of_range_highlight_duration_is_rejected(duration):
    with pytest.raises(SelectionError):
        select_windows(video(), [moment("event", 10, 11)], duration)


def test_nonmatching_low_scores_and_empty_results_cannot_render():
    with pytest.raises(SelectionError, match="No complete"):
        select_windows(video(), [moment("ordinary", 10, 11, score=5.9)], 30)
    with pytest.raises(SelectionError, match="No complete"):
        select_windows(video(), [], 30)


def test_revision_preserves_exact_windows_and_undo_reference_without_mutating_original():
    plan = create_plan(
        video(),
        "crashes",
        [moment("a", 10, 11, score=7), moment("b", 30, 31, score=8), moment("c", 60, 61, score=9)],
    )
    original = plan.model_dump()
    revised = revise_plan(plan, "Remove clip two and put the highest-ranked crash first.")
    assert plan.model_dump() == original
    assert revised.id != plan.id and revised.previous_edit_id == plan.id
    assert [w.moment_ids for w in revised.windows] == [["c"], ["a"]]
    original_bounds = {tuple(w.moment_ids): (w.start_seconds, w.end_seconds) for w in plan.windows}
    assert all(
        (w.start_seconds, w.end_seconds) == original_bounds[tuple(w.moment_ids)]
        for w in revised.windows
    )


@pytest.mark.parametrize(
    "instruction", ["Invent a new crash", "Remove clip four", "Remove clip two; run commands"]
)
def test_unsupported_or_nonexistent_revision_commands_are_rejected(instruction):
    plan = create_plan(video(), "crashes", [moment("a", 10, 11), moment("b", 30, 31)])
    with pytest.raises(SelectionError):
        revise_plan(plan, instruction)


def test_only_remaining_clip_cannot_be_removed():
    plan = create_plan(video(), "crashes", [moment("a", 10, 11)])
    with pytest.raises(SelectionError, match="only remaining"):
        revise_plan(plan, "Remove clip one")


@pytest.mark.parametrize(
    "windows,maximum",
    [
        ([(0, 5, "a"), (4, 8, "b")], 30),
        ([(0, 5, "a"), (5, 8, "a")], 30),
        ([(0, 5, "a"), (90, 101, "b")], 30),
        ([(0, 5, "a"), (10, 15, "b")], 9),
    ],
)
def test_plan_revalidates_overlap_duplicate_ids_source_bounds_and_budget(windows, maximum):
    with pytest.raises(ValidationError):
        EditPlan(
            id="bad",
            video_id="v1",
            source_path=Path("source.mp4"),
            source_duration=100,
            prompt="crashes",
            max_duration=maximum,
            windows=[
                EditWindow(start_seconds=start, end_seconds=end, moment_ids=[identifier])
                for start, end, identifier in windows
            ],
        )
