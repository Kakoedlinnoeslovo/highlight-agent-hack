"""Business reports preserve count meaning and fail clearly without provider results."""

from contextlib import nullcontext
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from highlight_agent.inference import InferenceError
from highlight_agent.models import SourceChunk, VideoRecord


@pytest.fixture
def business_report(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return import_module("business_report")


def report(*, recommend=False, counts=(2, 2)):
    ranking = [
        {
            "camera": f"Camera {index}",
            "count": count,
            "segments": 36,
            "strength": "some signal",
            "recorded": "1 Oct 2026",
        }
        for index, count in enumerate(counts, start=1)
    ]
    return {
        "corners": ranking,
        "minutes": 6,
        "seconds": 1,
        "presets": [
            {
                "key": "bike-shop",
                "icon": "B",
                "label": "Bike shop",
                "signal": "people riding bicycles",
                "why": "Compare cycling activity.",
                "recommend": recommend,
                "ranking": ranking,
                "evidence": [],
            }
        ],
    }


def test_sparse_evidence_reports_the_maximum_for_one_corner(business_report, tmp_path):
    destination = tmp_path / "index.html"
    business_report.render_html(report(), destination)
    page = destination.read_text()

    assert "Not enough evidence to recommend a corner" in page
    assert "The strongest corner has only 2 matching" in page
    assert "across all corners show" not in page


def test_equal_signals_keep_both_corners_in_the_verdict(business_report, tmp_path):
    destination = tmp_path / "index.html"
    business_report.render_html(report(recommend=True, counts=(15, 15)), destination)

    assert "Best corner: Camera 1 and Camera 2" in destination.read_text()


def test_bicycle_counterexample_does_not_generalize_to_every_nonmatch(business_report, tmp_path):
    payload = report(recommend=True, counts=(11, 9))
    (tmp_path / "trap.jpg").write_bytes(b"test still")
    payload["presets"][0]["trap"] = {
        "camera": "Camera 1",
        "naive_count": 25,
        "count": 1,
        "thumbnail": "trap.jpg",
        "sentence": "A bicycle is parked beside a bin.",
        "caption": "A bicycle is parked beside a bin.",
        "source": "chunk.mp4 @ 15.0 s",
    }
    destination = tmp_path / "index.html"
    business_report.render_html(payload, destination)
    page = destination.read_text()

    assert "A bicycle is parked beside a bin." in page
    assert "match the riding signal" in page
    assert "the rest describe" not in page
    assert "would put" not in page


def test_all_model_failures_do_not_publish_a_successful_report(
    business_report, monkeypatch, tmp_path, capsys
):
    config = SimpleNamespace(mode="live", missing=lambda: [], output_dir=tmp_path)
    video = SimpleNamespace(id="camera", dataset="sf-streets", index_status="ready")
    tracer = SimpleNamespace(
        initialize=lambda: None,
        span=lambda *args, **kwargs: nullcontext({"call": None, "url": None}),
    )
    service = SimpleNamespace(config=config, tracer=tracer, list_videos=lambda: [video])

    class ServiceFactory:
        def __new__(cls):
            return service

        @staticmethod
        def _verify_source(video):
            pass

    def unavailable(*args, **kwargs):
        raise InferenceError("Provider unavailable")

    monkeypatch.setattr(business_report, "HighlightService", ServiceFactory)
    monkeypatch.setattr(business_report, "InferenceClient", lambda config: object())
    monkeypatch.setattr(business_report, "_headlines", lambda *args: {})
    monkeypatch.setattr(business_report, "build_preset", unavailable)
    monkeypatch.setattr(business_report.city, "_camera_name", lambda video: "Camera 1")
    monkeypatch.setattr(business_report.city, "_recorded", lambda video: "1 Oct 2026")
    monkeypatch.setattr(business_report.city, "_moments", lambda *args: [])

    destination = tmp_path / "report"
    assert business_report.main(["--output-dir", str(destination)]) == 1
    assert "No business comparison could be generated" in capsys.readouterr().err
    assert not (destination / "index.html").exists()
    assert not (destination / "report.json").exists()


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (28, 38, "first.mp4 @ 28.0–30.0 s · second.mp4 @ 0.0–8.0 s"),
        (30, 35, "second.mp4 @ 0.0–5.0 s"),
        (0, 30, "first.mp4 @ 0.0–30.0 s"),
        (32, 38, "second.mp4 @ 2.0–8.0 s"),
    ],
)
def test_source_references_include_every_intersecting_chunk(business_report, start, end, expected):
    video = VideoRecord(
        id="street",
        label="SF Streets",
        local_path=Path("unused.mp4"),
        duration=60,
        original_video="s3://supplied/first.mp4",
        dataset="sf-streets",
        source_chunks=[
            SourceChunk(original_video="s3://supplied/first.mp4", start_seconds=0, duration=30),
            SourceChunk(original_video="s3://supplied/second.mp4", start_seconds=30, duration=30),
        ],
    )

    assert business_report.city._source_ref(video, start, end) == expected
