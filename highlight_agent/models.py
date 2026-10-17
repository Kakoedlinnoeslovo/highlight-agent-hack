"""Validated boundaries between providers, selection, and media rendering."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")


class SourceChunk(Record):
    """Exact dataset object and its measured offset in the local assembled recording."""

    original_video: str = Field(pattern=r"^s3://[^/\s]+/.+")
    start_seconds: float = Field(ge=0)
    duration: float = Field(gt=0)
    source_duration: float | None = Field(default=None, gt=0)
    source_sha256: str = ""

    @model_validator(mode="after")
    def included_interval(self):
        if self.source_duration is not None and self.duration > self.source_duration + 0.08:
            raise ValueError("An included dataset excerpt cannot exceed its original media.")
        return self


class VideoRecord(Record):
    id: str
    label: str
    local_path: Path
    duration: float = Field(gt=0)
    original_video: str | None = None
    index_status: Literal["local", "indexing", "ready", "error"] = "local"
    caption_model: str | None = None
    mode: Literal["live", "offline_sample"] = "live"
    content_sha256: str = ""
    error: str | None = None
    dataset: str | None = None
    source_chunks: list[SourceChunk] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_chunks(self):
        if self.source_chunks:
            end = 0.0
            seen: set[str] = set()
            for chunk in self.source_chunks:
                if chunk.original_video in seen or abs(chunk.start_seconds - end) > 0.05:
                    raise ValueError("Dataset chunks must be unique and contiguous.")
                seen.add(chunk.original_video)
                end += chunk.duration
            if abs(end - self.duration) > 0.1:
                raise ValueError("Dataset chunk durations must match the local recording.")
            if self.original_video != self.source_chunks[0].original_video:
                raise ValueError("The dataset source must identify its first exact chunk.")
        return self


class Moment(Record):
    id: str
    video_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    caption: str
    score: float = Field(default=0, ge=0, le=10)
    reason: str = ""
    evidence_type: Literal["caption_ranked", "video_verified", "manual"] = "caption_ranked"
    source: str | None = None
    category: str | None = None

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Moment end must be after its start.")
        return self


class EditWindow(Record):
    moment_ids: list[str] = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    score: float = Field(default=0, ge=0, le=10)
    caption: str = ""
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end_seconds - self.start_seconds

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Window end must be after its start.")
        if len(set(self.moment_ids)) != len(self.moment_ids):
            raise ValueError("A window cannot contain duplicate moment IDs.")
        return self


class EditPlan(Record):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    video_id: str
    source_path: Path
    source_duration: float = Field(gt=0)
    prompt: str
    windows: list[EditWindow] = Field(min_length=1)
    max_duration: float = Field(default=30, gt=0, le=180)
    previous_edit_id: str | None = None

    @property
    def duration(self) -> float:
        return sum(window.duration for window in self.windows)

    @model_validator(mode="after")
    def validate_windows(self):
        ordered = sorted(self.windows, key=lambda window: window.start_seconds)
        seen: set[str] = set()
        previous_end = 0.0
        for window in ordered:
            if window.start_seconds < previous_end - 0.00001:
                raise ValueError("Edit windows must not overlap, even when reordered.")
            if window.end_seconds > self.source_duration + 0.00001:
                raise ValueError("Edit window extends beyond the source.")
            if seen.intersection(window.moment_ids):
                raise ValueError("A moment cannot appear twice in an edit.")
            seen.update(window.moment_ids)
            previous_end = window.end_seconds
        if not math.isfinite(self.duration) or self.duration > self.max_duration + 0.00001:
            raise ValueError("Complete windows exceed the edit duration budget.")
        return self


class HighlightResult(Record):
    edit_plan: EditPlan
    output_path: Path
    moments: list[Moment]
    actual_duration: float | None = Field(default=None, gt=0)
    timings: dict[str, float] = Field(default_factory=dict)
    trace_url: str | None = None
    warnings: list[str] = Field(default_factory=list)
    mode: Literal["live", "offline_sample"] = "live"
