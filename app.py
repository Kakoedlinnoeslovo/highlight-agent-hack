"""The local Highlight Agent director's desk.

Run with ``python app.py`` after installing the project dependencies. The UI never
substitutes example results for a live run. Synthetic fixtures require the explicit
``HIGHLIGHT_MODE=offline_sample`` setting and remain labeled throughout the flow.
"""

from __future__ import annotations

import html
import os
import queue
import re
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import gradio as gr

from highlight_agent.service import HighlightService

PRO_BRIEF = "Make me look like a pro."
ROAST_BRIEF = "Now roast my driving."
REVISION = "Remove clip two and put the highest-ranked crash first."
ROOT = Path(__file__).resolve().parent

CSS = """
:root {color-scheme: dark; --ha-bg:#111210; --ha-panel:#1b1c19; --ha-line:#34362e;
 --ha-text:#f1f0e8; --ha-muted:#a6a99b; --ha-accent:#d4ef79;}
body, gradio-app, .gradio-container {background:var(--ha-bg)!important; color:var(--ha-text)!important;}
.gradio-container {width:100%!important;max-width:1400px!important;box-sizing:border-box;margin:auto;padding:30px 40px 22px!important;
 font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif!important;}
.gradio-container .main {padding:0!important;max-width:none!important;}
.gradio-container {--body-text-color:#f1f0e8!important;--block-background-fill:#1b1c19!important;
 --background-fill-primary:#111210!important;--background-fill-secondary:#21231c!important;
 --input-background-fill:#141511!important;--input-border-color:#34362e!important;
 --block-border-color:#34362e!important;--border-color-primary:#34362e!important;}
footer {display:none!important;}
.ha-masthead {display:flex; align-items:center; justify-content:space-between;
 border-bottom:1px solid var(--ha-line); padding:0 0 21px; margin-bottom:25px;}
.ha-brand {display:flex; gap:12px; align-items:center; font-size:16px; font-weight:650; letter-spacing:-.35px;}
.ha-brand, .ha-hero h1, .ha-section h2, .ha-empty h3 {color:var(--ha-text)!important;}
.ha-logo {width:32px;height:32px;border:1px solid var(--ha-accent);display:grid;place-items:center;
 color:var(--ha-accent);font:600 19px ui-monospace,monospace; transform:rotate(-5deg);}
.ha-tag {font:10px ui-monospace,monospace;letter-spacing:1.9px;color:var(--ha-muted);}
.ha-hero {display:flex;justify-content:space-between;gap:24px;align-items:end;margin:0 0 27px;}
.ha-eyebrow {color:var(--ha-accent);font:11px ui-monospace,monospace;letter-spacing:2px;margin-bottom:12px;}
.ha-hero h1 {font-size:clamp(34px,4vw,53px);line-height:1.04;letter-spacing:-2px;font-weight:550;margin:0;}
.ha-hero p {color:var(--ha-muted);font-size:14px;line-height:1.6;max-width:310px;margin:0 0 3px;}
.ha-section {display:flex;align-items:center;gap:10px;margin:0 0 14px;}
.ha-number {font:10px ui-monospace,monospace;color:var(--ha-accent);padding:5px;border:1px solid var(--ha-line);}
.ha-section h2 {font-size:14px;font-weight:600;letter-spacing:-.1px;margin:0;}
.ha-panel {background:var(--ha-panel)!important;border:1px solid var(--ha-line)!important;
 border-radius:10px!important;padding:20px!important;}
.ha-panel .ha-panel {padding:0!important;border:0!important;background:transparent!important;}
.ha-side {gap:24px!important;}
.ha-stack {gap:13px!important;}
.ha-panel .block, .ha-panel .form, .ha-panel .styler {border-color:var(--ha-line)!important;box-shadow:none!important;background:transparent!important;}
.ha-panel label, .ha-panel .label-wrap {color:var(--ha-muted)!important;}
.ha-panel input, .ha-panel textarea, .ha-panel select {color:var(--ha-text)!important;background:#141511!important;}
.ha-source-meta {display:flex;flex-wrap:wrap;gap:8px;margin:4px 0;color:var(--ha-muted);font-size:12px;}
.ha-chip {padding:4px 8px;border-radius:4px;background:#262821;color:#b8bcae;font:10px ui-monospace,monospace;}
.ha-chip.ready, .ha-chip.live {color:var(--ha-accent);}
.ha-brief {margin:0!important;padding:0!important;gap:0!important;border:1px solid var(--ha-line)!important;
 background:#21231c!important;border-radius:8px!important;overflow:hidden;}
.ha-brief .ha-brief {border:0!important;}
.ha-brief-copy {padding:15px 16px 11px;}
.ha-brief-copy .ha-small {font:10px ui-monospace,monospace;letter-spacing:1px;color:var(--ha-muted);}
.ha-brief-copy p {margin:7px 0 0;color:#b7bbab;font-size:12px;line-height:1.5;}
.ha-brief button {border:0!important;border-top:1px solid var(--ha-line)!important;border-radius:0!important;
 padding:11px 16px!important;text-align:left!important;justify-content:space-between!important;
 font-size:13px!important;font-weight:550!important;background:#292d20!important;color:var(--ha-accent)!important;}
.ha-brief button:hover {background:#333b23!important;}
.ha-generate {background:var(--ha-accent)!important;color:#1e2511!important;border:0!important;
 border-radius:6px!important;font-weight:650!important;min-height:43px!important;}
.ha-generate:hover {background:#e1fa96!important;}
.ha-secondary {background:#262820!important;color:var(--ha-text)!important;border:1px solid var(--ha-line)!important;
 border-radius:6px!important;font-size:12px!important;}
.ha-hint {font-size:11px;color:var(--ha-muted);line-height:1.55;}
.ha-empty {min-height:333px;background:#0c0d0b;border:1px solid #2e3127;border-radius:7px;
 display:flex;align-items:center;justify-content:center;text-align:center;position:relative;overflow:hidden;}
.ha-empty:before {content:"";position:absolute;inset:0;background:repeating-linear-gradient(0deg,transparent,
 transparent 42px,#22251a 43px,transparent 44px);opacity:.24;}
.ha-empty-inner {position:relative;max-width:290px;padding:28px;}
.ha-play {margin:0 auto 20px;width:51px;height:51px;border:1px solid #535b3b;border-radius:50%;display:grid;
 place-items:center;color:var(--ha-accent);font-size:21px;padding-left:3px;}
.ha-empty h3 {font-size:22px;font-weight:500;letter-spacing:-.5px;margin:0 0 10px;}
.ha-empty p {font-size:13px;line-height:1.6;color:var(--ha-muted);margin:0;}
.ha-result-meta {display:flex;gap:22px;flex-wrap:wrap;align-items:center;padding:11px 0 3px;}
.ha-stat {font:12px ui-monospace,monospace;color:var(--ha-text);}
.ha-stat span {color:var(--ha-muted);font:10px ui-monospace,monospace;display:block;margin-bottom:4px;}
.ha-stage {background:#141610;border-left:2px solid #60674b;padding:11px 13px;font-size:12px;line-height:1.6;
 color:#bfc4b2;min-height:45px;border-radius:0 4px 4px 0;}
.ha-stage.busy {border-color:var(--ha-accent);}
.ha-stage.error {border-color:#e8ac8e;color:#edc5b1;}
.ha-stage strong {font-weight:550;color:var(--ha-text);}
.ha-stage small {color:var(--ha-muted);display:block;font-size:10px;margin-top:2px;}
.ha-trace {font-size:11px;line-height:1.55;color:var(--ha-muted);}
.ha-trace a {color:var(--ha-accent);text-decoration:none;}
.ha-timeline {padding:13px 0 3px;}
.ha-timeline-head {display:flex;justify-content:space-between;gap:12px;font-size:11px;color:var(--ha-muted);margin-bottom:9px;}
.ha-timeline-head strong {color:var(--ha-text);font-weight:500;}
.ha-timeline svg {width:100%;height:auto;display:block;}
.ha-timeline .previous {animation:ha-fade .7s ease-out both;}
@keyframes ha-fade {from {opacity:.8;}to {opacity:.17;}}
.ha-receipts {margin-top:20px!important;}
#ha-moments table {min-width:850px!important;}
#ha-moments .table-container {overflow-x:auto!important;max-width:100%!important;}
#ha-moments .table-wrap {min-width:850px!important;}
#ha-moments th {background:#292c22!important;color:var(--ha-text)!important;border-color:var(--ha-line)!important;}
#ha-moments td {background:#171912!important;color:#d1d5c7!important;border-color:var(--ha-line)!important;}
#ha-moments td.selected {background:#303823!important;}
#ha-moments [role="gridcell"], #ha-moments .virtual-body [role="row"] {background:#171912!important;color:#d1d5c7!important;}
#ha-moments [role="gridcell"] * {background:transparent!important;color:#d1d5c7!important;}
.ha-provenance {padding:10px 13px;border:1px solid var(--ha-line);border-radius:5px;font-size:11px;
 line-height:1.6;color:var(--ha-muted);margin-bottom:17px;background:#171912;}
.ha-provenance strong {color:var(--ha-accent);font-weight:550;}
.ha-footer {display:flex;justify-content:space-between;gap:20px;border-top:1px solid var(--ha-line);
 color:#747a66;font-size:10px;margin-top:27px;padding-top:15px;line-height:1.6;}
.ha-footer b {font-weight:450;color:#b4b9a7;}
@media(max-width:800px) {.gradio-container{padding:20px 16px!important;}.ha-hero{display:block;}
 .ha-hero p{margin-top:17px;max-width:100%;}.ha-masthead{margin-bottom:23px;}.ha-tag{display:none;}
 .ha-panel{padding:15px!important;}.ha-empty{min-height:260px;}.ha-footer{display:block;}}
@media(prefers-reduced-motion:reduce) {.ha-timeline .previous{animation:none;opacity:.17;}}
"""


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _clock(seconds: float) -> str:
    seconds = max(0, int(float(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def _section(number: str, title: str) -> str:
    return f'<div class="ha-section"><span class="ha-number">{number}</span><h2>{_escape(title)}</h2></div>'


def _notice(message: str, *, error: bool = False, elapsed: float | None = None) -> str:
    suffix = f"<small>{elapsed:.1f}s elapsed</small>" if elapsed is not None else ""
    style = "error" if error else ("busy" if elapsed is not None else "")
    return f'<div class="ha-stage {style}">{_escape(message)}{suffix}</div>'


def _error_message(error: Exception) -> str:
    # Provider exceptions can contain bearer credentials. Never send these to a recording.
    message = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[redacted]", str(error))
    message = re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}\b", "[redacted]", message)
    return (message.strip() or "The request could not finish. Check the local server logs.")[:600]


def _provenance(status: dict[str, Any]) -> str:
    if status.get("mode") == "offline_sample":
        return (
            '<div class="ha-provenance"><strong>OFFLINE SAMPLE</strong> · Synthetic footage and a manual '
            "caption fixture. No VAST, Cosmos, W&amp;B Inference, or Weave calls are made.</div>"
        )
    missing = status.get("missing_config", status.get("missing", []))
    if missing:
        if isinstance(missing, dict):
            missing = list(missing)
        names = ", ".join(_escape(item) for item in missing)
        return (
            '<div class="ha-provenance"><strong>LIVE MODE · SETUP NEEDED</strong> · Configure '
            f"{names}. Your media can be registered before connecting the providers.</div>"
        )
    return (
        '<div class="ha-provenance"><strong>LIVE MODE</strong> · VAST search · Cosmos ingestion captions '
        "· W&amp;B query and ranking. Evidence and trace links appear after an actual run.</div>"
    )


def _video_metadata(video: Any | None) -> str:
    if video is None:
        return '<div class="ha-source-meta">Choose a source or add your recording below.</div>'
    status = str(_get(video, "index_status", "local"))
    model = _get(video, "caption_model")
    model_note = f'<span class="ha-chip">{_escape(model)}</span>' if model else ""
    return (
        f'<div class="ha-source-meta"><span class="ha-chip">{_clock(_get(video, "duration", 0))} source</span>'
        f'<span class="ha-chip {_escape(status)}">{_escape(status.upper())}</span>{model_note}</div>'
    )


def _timeline(
    duration: float = 0,
    windows: list[Any] | None = None,
    previous: list[dict[str, float]] | None = None,
) -> str:
    windows, previous = windows or [], previous or []
    total = max(float(duration), 1.0)
    rects = ['<rect x="0" y="6" width="1000" height="22" rx="4" fill="#2b2e23"/>']
    for old in previous:
        start = max(0.0, min(float(old.get("start_seconds", 0)), total))
        end = max(start, min(float(old.get("end_seconds", 0)), total))
        rects.append(
            f'<rect class="previous" x="{start / total * 1000:.2f}" y="6" '
            f'width="{max((end - start) / total * 1000, 1):.2f}" height="22" rx="3" fill="#c9c9b3"/>'
        )
    for index, window in enumerate(windows):
        start = max(0.0, min(float(_get(window, "start_seconds", 0)), total))
        end = max(start, min(float(_get(window, "end_seconds", 0)), total))
        rects.append(
            f'<rect x="{start / total * 1000:.2f}" y="6" '
            f'width="{max((end - start) / total * 1000, 1):.2f}" height="22" rx="3" fill="#d4ef79">'
            f"<title>Clip {index + 1}: {_clock(start)} to {_clock(end)}</title></rect>"
        )
    changed = len(windows)
    if previous:
        prior = {
            (round(item["start_seconds"], 2), round(item["end_seconds"], 2)) for item in previous
        }
        changed = sum(
            (
                round(float(_get(item, "start_seconds", 0)), 2),
                round(float(_get(item, "end_seconds", 0)), 2),
            )
            not in prior
            for item in windows
        )
    detail = (
        f"{changed} different moment{'s' if changed != 1 else ''} selected"
        if previous
        else (
            f"{len(windows)} moment{'s' if len(windows) != 1 else ''} selected"
            if windows
            else "Your selections appear here"
        )
    )
    labels = (
        f'<text x="0" y="50" fill="#929983" font-size="11">0:00</text>'
        f'<text x="500" y="50" text-anchor="middle" fill="#929983" font-size="11">{_clock(total / 2)}</text>'
        f'<text x="1000" y="50" text-anchor="end" fill="#929983" font-size="11">{_clock(duration)}</text>'
    )
    return (
        '<div class="ha-timeline"><div class="ha-timeline-head"><strong>Source timeline</strong>'
        f'<span>{detail}</span></div><svg viewBox="0 0 1000 56" role="img" '
        f'aria-label="Selected intervals in the source recording">{"".join(rects)}{labels}</svg></div>'
    )


def _trace_markup(result: Any) -> str:
    url = _get(result, "trace_url")
    if url and urlsplit(str(url)).scheme in {"http", "https"}:
        trace = f'<a href="{_escape(url)}" target="_blank" rel="noopener noreferrer">Open this run in Weave ↗</a>'
    else:
        trace = (
            "Offline fixture · no sponsor calls or trace"
            if _get(result, "mode") == "offline_sample"
            else "No Weave trace link was returned for this run."
        )
    warnings = _get(result, "warnings", []) or []
    warning_text = "".join(f"<br>{_escape(item)}" for item in warnings)
    return f'<div class="ha-trace">{trace}{warning_text}</div>'


def _result_rows(result: Any) -> list[list[Any]]:
    moments = {str(_get(item, "id")): item for item in (_get(result, "moments", []) or [])}
    rows = []
    for index, window in enumerate(_get(_get(result, "edit_plan"), "windows", [])):
        start, end = float(_get(window, "start_seconds", 0)), float(_get(window, "end_seconds", 0))
        ids = _get(window, "moment_ids", []) or []
        evidence = {
            _get(moments[str(item)], "evidence_type", "ingestion caption")
            for item in ids
            if str(item) in moments
        }
        rows.append(
            [
                index + 1,
                f"{_clock(start)} → {_clock(end)}",
                f"{end - start:.1f}s",
                _get(window, "caption", ""),
                _get(window, "reason", ""),
                ", ".join(sorted(evidence)) or "source interval",
            ]
        )
    return rows


def _request_seconds(
    timings: dict[str, float], elapsed: float, *, restored: bool
) -> float | None:
    measured = timings.get("total", timings.get("total_seconds"))
    if measured is not None:
        return float(measured)
    if restored:
        measured = timings.get("render")
        return float(measured) if measured is not None else None
    return elapsed


def _stream_job(
    operation: Callable[[Callable[[str], None]], Any],
) -> Iterator[tuple[str, Any, float]]:
    """Yield real provider progress while a synchronous pipeline is running."""
    messages: queue.Queue[tuple[str, Any]] = queue.Queue()
    started = time.monotonic()

    def worker() -> None:
        try:
            result = operation(lambda stage: messages.put(("stage", str(stage))))
            messages.put(("result", result))
        except Exception as error:
            messages.put(("error", error))

    threading.Thread(target=worker, name="highlight-ui-job", daemon=True).start()
    current_stage = "Starting the requested edit…"
    while True:
        try:
            kind, value = messages.get(timeout=0.5)
        except queue.Empty:
            yield "stage", current_stage, time.monotonic() - started
            continue
        if kind == "stage":
            current_stage = value
        yield kind, value, time.monotonic() - started
        if kind in {"result", "error"}:
            return


def build_app(service: HighlightService | None = None) -> gr.Blocks:
    """Construct the UI without contacting any external providers."""
    service = service or HighlightService()
    status = service.status()
    videos = service.list_videos()
    first = videos[0] if videos else None

    def video_by_id(video_id: str | None) -> Any | None:
        return next(
            (item for item in service.list_videos() if str(_get(item, "id")) == str(video_id)), None
        )

    def library(video: Any | None = None) -> tuple[Any, str]:
        items = service.list_videos()
        value = str(_get(video, "id")) if video else (str(_get(items[0], "id")) if items else None)
        return gr.update(
            choices=[
                (str(_get(item, "label", "Untitled source")), str(_get(item, "id")))
                for item in items
            ],
            value=value,
        ), _video_metadata(video or (items[0] if items else None))

    def choose_source(video_id: str | None, session: dict[str, Any]) -> tuple[Any, ...]:
        video = video_by_id(video_id)
        source_values = (
            _video_metadata(video),
            gr.update(open=not bool(video)),
            gr.update(value=str(_get(video, "local_path")) if video else None),
        )
        if session.get("video_id") == video_id:
            return (*source_values, *(gr.skip() for _ in range(12)))
        cleared = (
            {"edit_id": None, "video_id": video_id, "windows": []},
            gr.update(value=None, visible=False),
            gr.update(visible=True),
            gr.update(value=None, visible=False),
            _timeline(float(_get(video, "duration", 0))),
            [],
            '<div class="ha-result-meta"><div class="ha-stat"><span>OUTPUT</span>720p · MP4</div></div>',
            _notice("Source selected. Choose your brief to make a cut."),
            '<div class="ha-trace">Evidence and actual trace links appear after a run.</div>',
            gr.update(visible=False),
            gr.update(value=None, visible=False),
            '<p class="ha-hint">Select a moment after making a cut to inspect its original source interval.</p>',
        )
        return (*source_values, *cleared)

    def register_source(upload: str | None, label: str, original: str) -> tuple[Any, str, str]:
        try:
            if not upload:
                raise ValueError("Choose a video file first.")
            video = service.register_video(
                Path(upload), label=label.strip(), original_video=original.strip() or None
            )
            choices, metadata = library(video)
            return (
                choices,
                metadata,
                _notice("Source registered. Index it, or connect an existing VAST source."),
            )
        except Exception as error:
            return gr.skip(), gr.skip(), _notice(_error_message(error), error=True)

    def index_source(video_id: str | None, refresh: bool = False) -> tuple[str, str]:
        try:
            if not video_id:
                raise ValueError("Register or choose a source first.")
            video = service.refresh_index(video_id) if refresh else service.ingest_video(video_id)
            state = str(_get(video, "index_status", "local"))
            message = (
                "Index ready. Choose your creative brief."
                if state == "ready"
                else "Indexing requested. Refresh the index status before making a cut."
            )
            return _video_metadata(video), _notice(message)
        except Exception as error:
            return gr.skip(), _notice(_error_message(error), error=True)

    def add_sample() -> tuple[Any, str, str]:
        try:
            video = service.register_sample()
            choices, metadata = library(video)
            return (
                choices,
                metadata,
                _notice(
                    "Synthetic sample ready. These fixture captions do not prove sponsor integrations."
                ),
            )
        except Exception as error:
            return gr.skip(), gr.skip(), _notice(_error_message(error), error=True)

    def pack_result(
        result: Any, session: dict[str, Any], elapsed: float, restored: bool = False
    ) -> tuple[Any, ...]:
        plan = _get(result, "edit_plan")
        windows = list(_get(plan, "windows", []))
        duration = _get(result, "actual_duration") or sum(
            float(_get(item, "end_seconds", 0)) - float(_get(item, "start_seconds", 0))
            for item in windows
        )
        old = (
            session.get("windows", [])
            if session.get("video_id") == str(_get(plan, "video_id"))
            else []
        )
        new_session = {
            "edit_id": str(_get(plan, "id")),
            "video_id": str(_get(plan, "video_id")),
            "windows": [
                {
                    "start_seconds": float(_get(item, "start_seconds", 0)),
                    "end_seconds": float(_get(item, "end_seconds", 0)),
                }
                for item in windows
            ],
        }
        timings = _get(result, "timings", {}) or {}
        measured = _request_seconds(timings, elapsed, restored=restored)
        measured_text = f"{measured:.1f}s" if measured is not None else "Unavailable"
        mode = "OFFLINE SAMPLE" if _get(result, "mode") == "offline_sample" else "LIVE RUN"
        timing_label = "ORIGINAL REQUEST" if restored else "THIS REQUEST"
        if restored:
            mode = "SAVED " + mode
        metadata = (
            '<div class="ha-result-meta">'
            f'<div class="ha-stat"><span>DURATION</span>{duration:.1f}s</div>'
            f'<div class="ha-stat"><span>MOMENTS</span>{len(windows):02}</div>'
            f'<div class="ha-stat"><span>{timing_label}</span>{measured_text}</div>'
            f'<div class="ha-stat"><span>PROVENANCE</span>{mode}</div></div>'
        )
        output_path = str(_get(result, "output_path"))
        return (
            new_session,
            gr.update(value=output_path, visible=True),
            gr.update(visible=False),
            gr.update(value=output_path, visible=True),
            _timeline(float(_get(plan, "source_duration", 0)), windows, old),
            _result_rows(result),
            metadata,
            _notice(
                f"Previous edit restored. {len(windows)} complete moments · {duration:.1f}s."
                if restored
                else f"Your cut is ready. {len(windows)} complete moments · {duration:.1f}s."
            ),
            _trace_markup(result),
            gr.update(visible=True),
            gr.update(value=None, visible=False),
            '<p class="ha-hint">Select a moment below to play its exact source interval.</p>',
        )

    def job_updates(
        operation: Callable[[Callable[[str], None]], Any],
        session: dict[str, Any],
        restored: bool = False,
    ) -> Iterator[tuple[Any, ...]]:
        for kind, value, elapsed in _stream_job(operation):
            if kind == "result":
                yield pack_result(value, session, elapsed, restored=restored)
            else:
                values = [gr.skip() for _ in range(12)]
                values[7] = _notice(
                    _error_message(value) if kind == "error" else value,
                    error=kind == "error",
                    elapsed=elapsed,
                )
                yield tuple(values)

    def make_cut(
        video_id: str | None, prompt: str, max_duration: float, session: dict[str, Any]
    ) -> Iterator[tuple[Any, ...]]:
        if not video_id or not prompt.strip():
            values = [gr.skip() for _ in range(12)]
            values[7] = _notice("Choose a source and tell the editor what to look for.", error=True)
            yield tuple(values)
            return
        yield from job_updates(
            lambda progress: service.make_highlight(
                video_id, prompt.strip(), max_duration=float(max_duration), progress=progress
            ),
            session,
        )

    def revise(instruction: str, session: dict[str, Any]) -> Iterator[tuple[Any, ...]]:
        edit_id = session.get("edit_id")
        if not edit_id:
            values = [gr.skip() for _ in range(12)]
            values[7] = _notice("Make a cut before revising it.", error=True)
            yield tuple(values)
            return
        yield from job_updates(
            lambda progress: service.revise_edit(edit_id, instruction.strip(), progress=progress),
            session,
        )

    def pro_cut(
        video_id: str | None, max_duration: float, session: dict[str, Any]
    ) -> Iterator[tuple[Any, ...]]:
        for values in make_cut(video_id, PRO_BRIEF, max_duration, session):
            yield (PRO_BRIEF, *values)

    def roast_cut(
        video_id: str | None, max_duration: float, session: dict[str, Any]
    ) -> Iterator[tuple[Any, ...]]:
        for values in make_cut(video_id, ROAST_BRIEF, max_duration, session):
            yield (ROAST_BRIEF, *values)

    def undo(session: dict[str, Any]) -> Iterator[tuple[Any, ...]]:
        edit_id = session.get("edit_id")
        if not edit_id:
            return
        yield from job_updates(lambda progress: service.undo_edit(edit_id), session, restored=True)

    def preview(session: dict[str, Any], event: gr.SelectData) -> tuple[Any, str]:
        try:
            edit_id = session.get("edit_id")
            if not edit_id:
                return (
                    gr.skip(),
                    '<p class="ha-hint">Make a cut to inspect the original moments.</p>',
                )
            index = event.index[0] if isinstance(event.index, (tuple, list)) else event.index
            index = int(index)
            path = service.preview_moment(edit_id, index)
            result = service.get_result(edit_id)
            window = _get(_get(result, "edit_plan"), "windows")[index]
            start, end = (
                float(_get(window, "start_seconds", 0)),
                float(_get(window, "end_seconds", 0)),
            )
            label = (
                f'<p class="ha-hint"><strong>Original source · clip {index + 1}</strong> · '
                f"{_clock(start)} → {_clock(end)} · {_escape(_get(window, 'caption', ''))}</p>"
            )
            return gr.update(value=str(path), visible=True), label
        except Exception as error:
            return gr.skip(), _notice(_error_message(error), error=True)

    with gr.Blocks(title="Highlight Agent · Director’s desk", analytics_enabled=False) as app:
        session = gr.State({"edit_id": None, "video_id": None, "windows": []})
        gr.HTML(
            '<div class="ha-masthead"><div class="ha-brand"><span class="ha-logo">H</span>'
            'Highlight Agent</div><span class="ha-tag">DIRECTOR’S DESK / 001</span></div>'
        )
        gr.HTML(
            '<div class="ha-hero"><div><div class="ha-eyebrow">ONE SESSION. TWO STORIES.</div>'
            "<h1>Your footage.<br>Your director’s cut.</h1></div>"
            "<p>Tell the editor what to find.<br>See the moments. Make your cut.</p></div>"
        )
        gr.HTML(_provenance(status))

        with gr.Row(equal_height=False):
            with gr.Column(scale=4, min_width=300, elem_classes=["ha-side"]):
                with gr.Group(elem_classes=["ha-panel", "ha-stack"]):
                    gr.HTML(_section("01", "Choose your footage"))
                    sources = gr.Dropdown(
                        choices=[
                            (str(_get(item, "label", "Untitled source")), str(_get(item, "id")))
                            for item in videos
                        ],
                        value=str(_get(first, "id")) if first else None,
                        label="Source recording",
                        interactive=True,
                    )
                    source_meta = gr.HTML(_video_metadata(first))
                    with gr.Accordion("Preview original recording", open=False):
                        original_player = gr.Video(
                            value=str(_get(first, "local_path")) if first else None,
                            label="Original recording",
                            interactive=False,
                            elem_id="ha-original-source",
                        )
                    with gr.Accordion("Add a recording", open=not bool(first)) as add_recording:
                        upload = gr.File(label="Video file", file_types=["video"], type="filepath")
                        source_label = gr.Textbox(
                            label="Recording name",
                            placeholder="Saturday stunt session",
                            max_lines=1,
                        )
                        existing_source = gr.Textbox(
                            label="Existing VAST source (optional)",
                            placeholder="Exact original_video value from the index",
                            max_lines=1,
                        )
                        register = gr.Button("Add recording", elem_classes=["ha-secondary"])
                    with gr.Row():
                        ingest = gr.Button("Index source", size="sm", elem_classes=["ha-secondary"])
                        refresh = gr.Button(
                            "Refresh status", size="sm", elem_classes=["ha-secondary"]
                        )
                    if status.get("mode") == "offline_sample":
                        sample = gr.Button(
                            "Create synthetic sample", size="sm", elem_classes=["ha-secondary"]
                        )
                    else:
                        sample = None
                    source_notice = gr.HTML(
                        '<p class="ha-hint">Index once. Make different cuts from the same footage.</p>'
                    )

                with gr.Group(elem_classes=["ha-panel", "ha-stack"]):
                    gr.HTML(_section("02", "Give it a direction"))
                    with gr.Group(elem_classes=["ha-brief"]):
                        gr.HTML(
                            '<div class="ha-brief-copy"><div class="ha-small">THE HIGHLIGHT REEL</div>'
                            "<p>Clean landings. Still upright. Keep driving.</p></div>"
                        )
                        pro = gr.Button("Make me look like a pro.  ↗")
                    with gr.Group(elem_classes=["ha-brief"]):
                        gr.HTML(
                            '<div class="ha-brief-copy"><div class="ha-small">THE HONEST VERSION</div>'
                            "<p>Hard impacts. Rollovers. Plans gone wrong.</p></div>"
                        )
                        roast = gr.Button("Now roast my driving.  ↗")
                    prompt = gr.Textbox(
                        label="Or write your own brief",
                        lines=2,
                        placeholder="Find the hardest crashes and save the biggest for last.",
                    )
                    budget = gr.Slider(
                        minimum=10,
                        maximum=30,
                        step=5,
                        value=30,
                        label="Maximum reel length",
                        info="Complete events can make a shorter cut.",
                    )
                    generate = gr.Button(
                        "Make my cut  →", variant="primary", elem_classes=["ha-generate"]
                    )

            with gr.Column(scale=7, min_width=340):
                with gr.Group(elem_classes=["ha-panel", "ha-stack"]):
                    gr.HTML(_section("03", "The director’s cut"))
                    empty = gr.HTML(
                        '<div class="ha-empty"><div class="ha-empty-inner"><div class="ha-play">▷</div>'
                        "<h3>Every session has a story.</h3><p>Choose a recording and a creative brief."
                        "<br>Your finished reel will play here.</p></div></div>"
                    )
                    player = gr.Video(
                        label="Your compilation",
                        visible=False,
                        interactive=False,
                        buttons=[],
                        elem_id="ha-result-video",
                    )
                    result_meta = gr.HTML(
                        '<div class="ha-result-meta"><div class="ha-stat"><span>OUTPUT</span>'
                        '720p · MP4</div><div class="ha-stat"><span>PACE</span>Complete moments</div></div>'
                    )
                    stage = gr.HTML(_notice("Ready when your source is. Choose a brief to start."))
                    download = gr.DownloadButton(
                        "Download your cut  ↓", visible=False, elem_classes=["ha-secondary"]
                    )
                    timeline = gr.HTML(_timeline(float(_get(first, "duration", 0))))
                    trace = gr.HTML(
                        '<div class="ha-trace">Caption evidence, measured timings, and actual trace links appear after a run.</div>'
                    )
                    with gr.Group(visible=False) as revision:
                        with gr.Accordion("One more edit…", open=False):
                            revision_prompt = gr.Textbox(
                                value=REVISION,
                                label="Revision",
                                lines=2,
                                info="Remove a numbered clip and reorder by its existing score.",
                            )
                            with gr.Row():
                                revise_button = gr.Button(
                                    "Apply revision", elem_classes=["ha-secondary"]
                                )
                                undo_button = gr.Button("Undo edit", elem_classes=["ha-secondary"])

        with gr.Group(elem_classes=["ha-panel", "ha-receipts", "ha-stack"]):
            gr.HTML(_section("04", "See why it made the cut"))
            evidence_label = gr.HTML(
                '<p class="ha-hint">Every selection keeps its source timestamp. Select a row to inspect the original action.</p>'
            )
            moments = gr.Dataframe(
                headers=[
                    "Clip",
                    "Source time",
                    "Length",
                    "What happens",
                    "Why selected",
                    "Evidence",
                ],
                datatype=["number", "str", "str", "str", "str", "str"],
                value=[],
                interactive=False,
                wrap=True,
                label="Selected moments",
                elem_id="ha-moments",
                column_widths=["6%", "14%", "8%", "28%", "28%", "16%"],
            )
            evidence_video = gr.Video(
                label="Original source interval",
                visible=False,
                interactive=False,
                elem_id="ha-source-preview",
            )
        gr.HTML(
            '<div class="ha-footer"><span><b>Highlight Agent</b> · One stream, different stories.</span>'
            "<span>Caption-based selections · Source intervals stay inspectable</span></div>"
        )

        run_outputs = [
            session,
            player,
            empty,
            download,
            timeline,
            moments,
            result_meta,
            stage,
            trace,
            revision,
            evidence_video,
            evidence_label,
        ]
        event_options = {
            "concurrency_id": "highlight-work",
            "concurrency_limit": 1,
            "api_visibility": "private",
        }
        app.load(lambda: library(), outputs=[sources, source_meta], **event_options)
        sources.change(
            choose_source,
            inputs=[sources, session],
            outputs=[source_meta, add_recording, original_player, *run_outputs],
            **event_options,
        )
        register.click(
            register_source,
            [upload, source_label, existing_source],
            [sources, source_meta, source_notice],
            **event_options,
        )
        ingest.click(index_source, [sources], [source_meta, source_notice], **event_options)
        refresh.click(
            lambda video_id: index_source(video_id, refresh=True),
            [sources],
            [source_meta, source_notice],
            **event_options,
        )
        if sample is not None:
            sample.click(add_sample, outputs=[sources, source_meta, source_notice], **event_options)
        generate.click(make_cut, [sources, prompt, budget, session], run_outputs, **event_options)
        pro.click(pro_cut, [sources, budget, session], [prompt, *run_outputs], **event_options)
        roast.click(roast_cut, [sources, budget, session], [prompt, *run_outputs], **event_options)
        revise_button.click(revise, [revision_prompt, session], run_outputs, **event_options)
        undo_button.click(undo, [session], run_outputs, **event_options)
        moments.select(preview, [session], [evidence_video, evidence_label], **event_options)

    app.queue(default_concurrency_limit=1, max_size=16)
    return app


def main() -> None:
    service = HighlightService()
    app = build_app(service)
    config = getattr(service, "config", None)
    allowed = [
        Path(_get(config, "data_dir", os.getenv("HIGHLIGHT_DATA_DIR", ROOT / "data"))).resolve(),
        Path(
            _get(config, "output_dir", os.getenv("HIGHLIGHT_OUTPUT_DIR", ROOT / "outputs"))
        ).resolve(),
    ]
    theme = gr.themes.Base(
        primary_hue="lime", neutral_hue="stone", font=["Inter", "Arial", "sans-serif"]
    ).set(
        body_background_fill="#111210",
        body_background_fill_dark="#111210",
        body_text_color="#f1f0e8",
        body_text_color_dark="#f1f0e8",
        block_background_fill="#1b1c19",
        block_background_fill_dark="#1b1c19",
        input_background_fill="#141511",
        input_background_fill_dark="#141511",
        border_color_primary="#34362e",
        border_color_primary_dark="#34362e",
        button_secondary_background_fill="#262820",
        button_secondary_background_fill_dark="#262820",
        button_secondary_text_color="#f1f0e8",
        button_secondary_text_color_dark="#f1f0e8",
    )
    app.launch(
        server_name="127.0.0.1",
        server_port=int(os.getenv("PORT", "7860")),
        share=False,
        theme=theme,
        css=CSS,
        allowed_paths=[str(item) for item in allowed],
        show_error=False,
    )


if __name__ == "__main__":
    main()
