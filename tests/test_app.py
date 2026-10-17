"""Keep saved edit timing separate from the time spent restoring it."""

import pytest

from app import _request_seconds


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
