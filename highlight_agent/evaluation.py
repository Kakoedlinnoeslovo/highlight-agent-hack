"""Human-reviewed caption evaluation using actual indexed evidence, never keyword accuracy."""

from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import Record
from .storage import read_json

Label = Literal["crash", "clean_landing", "people", "traffic", "ordinary"]


class EvaluationCase(Record):
    label: Label
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    caption_correct: bool | None = Field(default=None, strict=True)
    observed_label: Label | None = None
    notes: str = ""

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Evaluation interval must have a positive duration.")
        return self


class EvaluationManifest(Record):
    video_id: str
    profile: Literal["gameplay", "sf-streets"] = "gameplay"
    cases: list[EvaluationCase]

    @model_validator(mode="after")
    def six_balanced_cases(self):
        if self.profile == "sf-streets":
            expected = {"people": 2, "traffic": 2, "ordinary": 2}
            description = "two pedestrians/cyclists, two buses/traffic, two ordinary"
        else:
            expected = {"crash": 2, "clean_landing": 2, "ordinary": 2}
            description = "two crashes, two clean landings, two ordinary"
        if Counter(case.label for case in self.cases) != expected:
            raise ValueError(
                f"Use exactly six cases for {self.profile}: {description}."
            )
        return self


def evaluate_manifest(path: Path, service) -> dict:
    manifest = EvaluationManifest.model_validate(read_json(path))
    video = service.get_video(manifest.video_id)
    segments = service.indexed_segments(video.id)
    cases = []
    for case in manifest.cases:
        if case.end_seconds > video.duration + 0.00001:
            raise ValueError("An evaluation case extends beyond the registered local source.")
        evidence = [
            {
                "id": row["id"],
                "start_seconds": row["start_seconds"],
                "end_seconds": row["end_seconds"],
                "caption": row["caption"],
            }
            for row in segments
            if float(row["start_seconds"]) < case.end_seconds
            and float(row["end_seconds"]) > case.start_seconds
        ]
        cases.append({**case.model_dump(mode="json"), "indexed_evidence": evidence})
    reviewed = all(
        case["caption_correct"] is not None
        and case["observed_label"] is not None
        and bool(case["indexed_evidence"])
        for case in cases
    )
    correct = sum(
        case["caption_correct"] is True and case["observed_label"] == case["label"]
        for case in cases
    )
    positive_types = {
        case["label"]
        for case in cases
        if case["caption_correct"] is True and case["observed_label"] == case["label"]
    }
    false_clean = any(
        case["label"] == "crash" and case["observed_label"] == "clean_landing" for case in cases
    )
    required_types = (
        {"people", "traffic"} if manifest.profile == "sf-streets" else {"crash", "clean_landing"}
    )
    passed = (
        reviewed
        and correct >= 5
        and required_types <= positive_types
        and not false_clean
    )
    return {
        "video_id": video.id,
        "profile": manifest.profile,
        "mode": video.mode,
        "evaluation_kind": "human_review_of_indexed_captions",
        "status": "needs_review" if not reviewed else "passed" if passed else "failed",
        "correct": correct,
        "total": 6,
        "reported_caption_model": video.caption_model,
        "no_crash_mislabeled_clean": not false_clean,
        "cases": cases,
        "note": "Synthetic fixtures validate plumbing only; they do not measure source understanding."
        if video.mode == "offline_sample"
        else "Human annotations must be checked against the source video.",
    }
