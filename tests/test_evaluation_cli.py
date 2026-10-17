"""Check the human evidence gate and command line success/failure semantics."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from highlight_agent import cli
from highlight_agent.config import Settings
from highlight_agent.evaluation import EvaluationManifest, evaluate_manifest
from highlight_agent.inference import InferenceError
from highlight_agent.models import VideoRecord


class EvaluationService:
    def __init__(self, video, segments):
        self.video = video
        self.segments = segments

    def get_video(self, video_id):
        assert video_id == self.video.id
        return self.video

    def indexed_segments(self, video_id):
        assert video_id == self.video.id
        return self.segments


@pytest.fixture
def evaluation(tmp_path):
    video = VideoRecord(
        id="evaluation-source",
        label="Reviewed source",
        local_path=tmp_path / "source.mp4",
        duration=30,
        mode="live",
        caption_model="nvidia/Cosmos-Reason2-8B",
    )
    cases, segments = [], []
    labels = ["crash", "crash", "clean_landing", "clean_landing", "ordinary", "ordinary"]
    for index, label in enumerate(labels):
        start = index * 4
        cases.append(
            {
                "label": label,
                "start_seconds": start,
                "end_seconds": start + 3,
                "caption_correct": True,
                "observed_label": label,
            }
        )
        segments.append(
            {
                "id": f"evidence-{index}",
                "start_seconds": start,
                "end_seconds": start + 3,
                # Deliberately omit label keywords: correctness comes from source
                # review, not string matching inside captions.
                "caption": f"Human-reviewed visual observation {index + 1}.",
            }
        )
    manifest = tmp_path / "evaluation.json"
    payload = {"video_id": video.id, "cases": cases}
    return manifest, payload, EvaluationService(video, segments)


def run_evaluation(bundle):
    path, payload, service = bundle
    path.write_text(json.dumps(payload))
    return evaluate_manifest(path, service)


@pytest.fixture
def street_evaluation(evaluation):
    evaluation[1]["profile"] = "sf-streets"
    labels = ["people", "people", "traffic", "traffic", "ordinary", "ordinary"]
    for case, label in zip(evaluation[1]["cases"], labels, strict=True):
        case["label"] = label
        case["observed_label"] = label
    return evaluation


def test_sf_streets_source_reviewed_cases_pass_with_both_scene_types(street_evaluation):
    report = run_evaluation(street_evaluation)
    assert report["status"] == "passed" and report["correct"] == 6
    assert report["profile"] == "sf-streets"
    assert report["no_crash_mislabeled_clean"] is True
    assert all(case["indexed_evidence"] for case in report["cases"])


def test_sf_streets_one_people_mismatch_is_permitted_by_threshold(street_evaluation):
    case = street_evaluation[1]["cases"][0]
    case["caption_correct"] = False
    case["observed_label"] = "traffic"
    report = run_evaluation(street_evaluation)
    assert report["correct"] == 5 and report["status"] == "passed"


def test_sf_streets_one_incorrect_ordinary_case_still_passes(street_evaluation):
    case = street_evaluation[1]["cases"][4]
    case["caption_correct"] = False
    case["observed_label"] = "traffic"
    report = run_evaluation(street_evaluation)
    assert report["status"] == "passed" and report["correct"] == 5


@pytest.mark.parametrize("label", ["people", "traffic"])
def test_sf_streets_requires_recognition_of_both_scene_types(street_evaluation, label):
    for case in street_evaluation[1]["cases"]:
        if case["label"] == label:
            case["caption_correct"] = False
            case["observed_label"] = "ordinary"
    report = run_evaluation(street_evaluation)
    assert report["status"] == "failed" and report["correct"] == 4


@pytest.mark.parametrize("missing_field", ["caption_correct", "observed_label"])
def test_sf_streets_unreviewed_cases_remain_pending(street_evaluation, missing_field):
    street_evaluation[1]["cases"][0][missing_field] = None
    assert run_evaluation(street_evaluation)["status"] == "needs_review"


def test_sf_streets_requires_indexed_caption_evidence(street_evaluation):
    street_evaluation[2].segments.pop(0)
    report = run_evaluation(street_evaluation)
    assert report["status"] == "needs_review"
    assert report["cases"][0]["indexed_evidence"] == []


def test_sf_streets_profile_rejects_gameplay_case_labels(evaluation):
    evaluation[1]["profile"] = "sf-streets"
    with pytest.raises(ValueError, match="two pedestrians/cyclists, two buses/traffic"):
        run_evaluation(evaluation)


def test_street_labels_require_an_explicit_sf_streets_profile(street_evaluation):
    del street_evaluation[1]["profile"]
    with pytest.raises(ValueError, match="two crashes, two clean landings"):
        run_evaluation(street_evaluation)


@pytest.mark.parametrize(
    "expected_status,exit_code", [("passed", 0), ("failed", 1), ("needs_review", 2)]
)
def test_sf_streets_cli_reports_profile_and_human_review_gate(
    street_evaluation, monkeypatch, capsys, expected_status, exit_code
):
    path, payload, service = street_evaluation
    if expected_status == "failed":
        for case in payload["cases"][:2]:
            case["caption_correct"] = False
            case["observed_label"] = "traffic"
    elif expected_status == "needs_review":
        payload["cases"][0]["caption_correct"] = None
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(cli, "HighlightService", lambda config: service)
    monkeypatch.setattr(cli.Settings, "from_env", lambda: Settings())

    assert cli.main(["evaluate", str(path)]) == exit_code

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == expected_status
    assert report["profile"] == "sf-streets"


def test_sf_streets_template_contains_illustrative_intervals_without_review_claims():
    path = Path(__file__).resolve().parents[1] / "examples" / "evaluation-sf-streets.json"
    manifest = EvaluationManifest.model_validate_json(path.read_text())
    assert manifest.video_id == "REPLACE_WITH_IMPORTED_VIDEO_ID"
    assert manifest.profile == "sf-streets"
    assert len(manifest.cases) == 6
    assert all(case.caption_correct is None and case.observed_label is None for case in manifest.cases)
    assert all("Illustrative interval" in case.notes for case in manifest.cases)


def test_six_source_reviewed_cases_pass_with_indexed_evidence(evaluation):
    report = run_evaluation(evaluation)
    assert report["status"] == "passed" and report["correct"] == 6
    assert report["evaluation_kind"] == "human_review_of_indexed_captions"
    assert report["reported_caption_model"] == "nvidia/Cosmos-Reason2-8B"
    assert report["profile"] == "gameplay"
    assert all(case["indexed_evidence"] for case in report["cases"])


@pytest.mark.parametrize("missing_field", ["caption_correct", "observed_label"])
def test_unreviewed_case_remains_pending(evaluation, missing_field):
    evaluation[1]["cases"][0][missing_field] = None
    report = run_evaluation(evaluation)
    assert report["status"] == "needs_review"


def test_human_review_without_indexed_caption_evidence_remains_pending(evaluation):
    evaluation[2].segments.pop(0)
    report = run_evaluation(evaluation)
    assert report["status"] == "needs_review"
    assert report["cases"][0]["indexed_evidence"] == []


def test_crash_mislabeled_clean_fails_even_with_five_other_correct_cases(evaluation):
    case = evaluation[1]["cases"][0]
    case["caption_correct"] = False
    case["observed_label"] = "clean_landing"
    report = run_evaluation(evaluation)
    assert report["correct"] == 5 and report["status"] == "failed"
    assert report["no_crash_mislabeled_clean"] is False


def test_one_incorrect_ordinary_case_is_permitted_by_five_of_six_threshold(evaluation):
    case = evaluation[1]["cases"][4]
    case["caption_correct"] = False
    case["observed_label"] = "crash"
    report = run_evaluation(evaluation)
    assert report["status"] == "passed" and report["correct"] == 5


def test_two_incorrect_ordinary_cases_fail_minimum_accuracy(evaluation):
    for case in evaluation[1]["cases"][4:]:
        case["caption_correct"] = False
        case["observed_label"] = "crash"
    report = run_evaluation(evaluation)
    assert report["status"] == "failed" and report["correct"] == 4


def test_label_mismatch_cannot_be_marked_correct_by_boolean_alone(evaluation):
    evaluation[1]["cases"][0]["observed_label"] = "clean_landing"
    report = run_evaluation(evaluation)
    assert report["correct"] == 5 and report["status"] == "failed"


def test_source_bounds_and_balanced_case_requirements_are_enforced(evaluation):
    evaluation[1]["cases"][0]["end_seconds"] = 31
    with pytest.raises(ValueError, match="extends beyond"):
        run_evaluation(evaluation)
    evaluation[1]["cases"].pop()
    with pytest.raises(ValueError, match="exactly six"):
        run_evaluation(evaluation)


@pytest.mark.parametrize(
    "expected_status,exit_code", [("passed", 0), ("failed", 1), ("needs_review", 2)]
)
def test_evaluate_cli_exit_codes_and_saved_report(
    evaluation, monkeypatch, capsys, expected_status, exit_code
):
    path, payload, service = evaluation
    if expected_status == "failed":
        payload["cases"][0]["caption_correct"] = False
        payload["cases"][0]["observed_label"] = "clean_landing"
    elif expected_status == "needs_review":
        payload["cases"][0]["caption_correct"] = None
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(cli, "HighlightService", lambda config: service)
    monkeypatch.setattr(cli.Settings, "from_env", lambda: Settings())
    destination = path.parent / "saved-report.json"
    assert cli.main(["evaluate", str(path), "--output", str(destination)]) == exit_code
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["status"] == expected_status
    assert json.loads(destination.read_text()) == emitted


def test_cli_reports_invalid_manifest_as_user_facing_error(evaluation, monkeypatch, capsys):
    path, payload, service = evaluation
    path.write_text("{ invalid json")
    monkeypatch.setattr(cli, "HighlightService", lambda config: service)
    monkeypatch.setattr(cli.Settings, "from_env", lambda: Settings())
    assert cli.main(["evaluate", str(path)]) == 1
    captured = capsys.readouterr()
    assert "Highlight Agent:" in captured.err and not captured.out


def test_remote_doctor_error_lists_make_cli_fail(monkeypatch, capsys):
    service = object()
    monkeypatch.setattr(cli, "HighlightService", lambda config: service)
    monkeypatch.setattr(cli.Settings, "from_env", lambda: Settings())
    monkeypatch.setattr(
        cli,
        "doctor",
        lambda target, remote: {
            "missing": [],
            "tools": {"ffmpeg": True, "ffprobe": True},
            "remote": {
                "VAST": {"errors": ["VAST request timed out."]},
                "W&B Inference": {"ok": True},
            },
        },
    )
    assert cli.main(["doctor", "--remote"]) == 1
    assert json.loads(capsys.readouterr().out)["remote"]["VAST"]["errors"]


def test_doctor_captures_remote_inference_failures_without_crashing(monkeypatch):
    class Tracer:
        warning = None

        def initialize(self):
            pass

        @contextmanager
        def span(self, *args):
            yield {"url": None}

    class Inference:
        def structured(self, *args):
            raise InferenceError("Inference request timed out.")

    service = SimpleNamespace(
        config=Settings(mode="live"),
        tracer=Tracer(),
        status=lambda: {"missing": []},
        _vast_client=lambda: SimpleNamespace(doctor=lambda: {"errors": []}),
        _inference_client=lambda: Inference(),
    )
    monkeypatch.setattr(cli.shutil, "which", lambda tool: "/usr/bin/" + tool)
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=" T.. scale V->V\n")
    )
    report = cli.doctor(service, remote=True)
    assert report["remote"]["W&B Inference"]["error"] == "Inference request timed out."
    assert report["remote"]["VAST"]["errors"] == []


def test_doctor_without_remote_never_calls_providers(monkeypatch):
    def unexpected():
        pytest.fail("Local doctor must not call a remote provider")

    service = SimpleNamespace(
        config=Settings(mode="live"),
        status=lambda: {"missing": []},
        _vast_client=unexpected,
        _inference_client=unexpected,
    )
    monkeypatch.setattr(cli.shutil, "which", lambda tool: None)
    report = cli.doctor(service, remote=False)
    assert "remote" not in report
    assert report["tools"] == {"ffmpeg": False, "ffprobe": False}
