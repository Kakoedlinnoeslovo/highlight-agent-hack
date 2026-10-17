"""Application-owned edit decisions: finite intervals, complete events, no repeats."""

from __future__ import annotations

import re
from uuid import uuid4

from .models import EditPlan, EditWindow, Moment, VideoRecord


class SelectionError(ValueError):
    pass


def _merge_windows(windows: list[EditWindow]) -> list[EditWindow]:
    merged: list[EditWindow] = []
    for window in sorted(windows, key=lambda item: item.start_seconds):
        if merged and window.start_seconds < merged[-1].end_seconds:
            previous = merged[-1]
            merged[-1] = EditWindow(
                moment_ids=previous.moment_ids + window.moment_ids,
                start_seconds=previous.start_seconds,
                end_seconds=max(previous.end_seconds, window.end_seconds),
                score=max(previous.score, window.score),
                caption=previous.caption + " · " + window.caption,
                reason=previous.reason + " · " + window.reason,
            )
        else:
            merged.append(window)
    return merged


def select_windows(
    video: VideoRecord, moments: list[Moment], max_duration: float
) -> list[EditWindow]:
    if not 0 < max_duration <= 30:
        raise SelectionError("Highlight duration must be greater than zero and at most 30 seconds.")
    # Padding is fixed and auditable. Models cannot invent an event's peak or edit bounds.
    candidates: list[EditWindow] = []
    seen: set[str] = set()
    for moment in sorted(moments, key=lambda item: item.start_seconds):
        if moment.id in seen or moment.score < 6:
            continue
        seen.add(moment.id)
        if moment.video_id != video.id or moment.end_seconds > video.duration + 0.001:
            raise SelectionError("A candidate does not belong to the registered source interval.")
        candidates.append(
            EditWindow(
                moment_ids=[moment.id],
                start_seconds=max(0, moment.start_seconds - 2),
                end_seconds=min(video.duration, moment.end_seconds + 3),
                score=moment.score,
                caption=moment.caption,
                reason=moment.reason,
            )
        )
    selected: list[EditWindow] = []
    for candidate in sorted(candidates, key=lambda item: (-item.score, item.start_seconds)):
        if video.dataset == "sf-streets":
            # Dense street captions often describe adjacent five-second slices.
            # Keep separate complete moments so the reel can still be revised.
            if any(
                candidate.start_seconds < window.end_seconds
                and candidate.end_seconds > window.start_seconds
                for window in selected
            ):
                continue
            proposal = [*selected, candidate]
        else:
            proposal = _merge_windows([*selected, candidate])
        # Admit complete padded events in rank order. A neighboring match must
        # not turn an already feasible edit into an over-budget merged interval.
        if (
            len(proposal) <= 4
            and sum(window.duration for window in proposal) <= max_duration + 0.00001
        ):
            selected = proposal
    if not selected:
        raise SelectionError(
            "No complete matching moments fit this duration. Try a longer duration or another brief."
        )
    # Build anticipation. The bounded revision can deliberately move the strongest to the opening.
    return sorted(selected, key=lambda item: (item.score, item.start_seconds))


def create_plan(
    video: VideoRecord, prompt: str, moments: list[Moment], max_duration=30
) -> EditPlan:
    return EditPlan(
        id=uuid4().hex,
        video_id=video.id,
        source_path=video.local_path,
        source_duration=video.duration,
        prompt=prompt,
        windows=select_windows(video, moments, max_duration),
        max_duration=max_duration,
    )


def revise_plan(plan: EditPlan, instruction: str) -> EditPlan:
    """Two supported commands, parsed without an unconstrained agent or new timestamp inference."""
    text = instruction.lower().strip().rstrip(".! ")
    strongest = r"(?:put|move) (?:the )?(?:highest[- ]ranked|biggest|strongest)(?: crash| clip| moment)? first"
    remove = r"remove (?:clip )?(one|two|three|four|[1-4])"
    match = re.fullmatch(rf"(?:{remove})(?: and {strongest})?", text)
    reorder = re.fullmatch(strongest, text)
    if not match and not reorder:
        raise SelectionError(
            "Supported revisions: 'Remove clip two', 'Put the highest-ranked clip first', "
            "or both joined by 'and'."
        )
    windows = [window.model_copy(deep=True) for window in plan.windows]
    if match:
        words = {"one": 1, "two": 2, "three": 3, "four": 4}
        number = words.get(match[1], int(match[1]) if match[1].isdigit() else 0)
        if not 1 <= number <= len(windows):
            raise SelectionError("That clip number is not in the current edit.")
        if len(windows) == 1:
            raise SelectionError("The only remaining clip cannot be removed.")
        windows.pop(number - 1)
    if reorder or " and " in text:
        best = max(range(len(windows)), key=lambda index: windows[index].score)
        windows.insert(0, windows.pop(best))
    values = plan.model_dump()
    values.update(id=uuid4().hex, windows=windows, previous_edit_id=plan.id)
    return EditPlan.model_validate(values)
