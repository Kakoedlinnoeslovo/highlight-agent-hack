"""Local, frame-accurate media rendering shared by highlights and demo edits."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .models import EditPlan, EditWindow


class MediaError(RuntimeError):
    """A readable failure to probe or render local media."""


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    has_audio: bool


def _run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Use argument arrays, bounded execution, and a short diagnostic on failure."""
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"{command[0]} is not installed or is not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{command[0]} timed out after {timeout:g} seconds.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "No diagnostic was returned.").strip()
        raise MediaError(f"{command[0]} failed: {detail[-1800:]}") from exc


def _probe_json(path: Path) -> dict:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise MediaError(f"Media file does not exist: {path}")
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        timeout=30,
    )
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MediaError("ffprobe returned an invalid media description.") from exc


def _duration(data: dict) -> float:
    candidates = [data.get("format", {}).get("duration")]
    candidates.extend(stream.get("duration") for stream in data.get("streams", []))
    for candidate in candidates:
        try:
            duration = float(candidate)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0:
            return duration
    raise MediaError("The media file has no usable duration.")


def _timestamp(value: object, default: float) -> float:
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return default


def _video_duration(path: Path, video: dict, container: dict) -> float:
    # Source intervals use the common input origin, including any video delay.
    origin = _timestamp(container.get("start_time"), 0.0)
    video_start = _timestamp(video.get("start_time"), origin)
    try:
        end = video_start + _duration({"streams": [video]}) - origin
        if end > 0:
            return end
    except MediaError:
        pass
    # Containers such as Matroska may omit stream duration. Inspect video
    # packet timestamps rather than trusting a container extended by audio.
    result = _run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "packet=pts_time,duration_time", "-of", "json",
            str(Path(path).expanduser().resolve()),
        ],
        timeout=30,
    )
    try:
        packets = json.loads(result.stdout)["packets"]
        ends = []
        for packet in packets:
            start = float(packet["pts_time"])
            duration = float(packet["duration_time"])
            if math.isfinite(start) and math.isfinite(duration) and duration > 0:
                ends.append(start + duration)
        if ends and max(ends) > origin:
            return max(ends) - origin
    except (KeyError, TypeError, ValueError):
        pass
    raise MediaError("The video stream has no usable duration.")


def probe_media(path: Path) -> MediaInfo:
    """Inspect a local video without loading it into Python memory."""
    data = _probe_json(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("The selected media has no video stream.")
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if width <= 0 or height <= 0:
        raise MediaError("The video has invalid dimensions.")
    return MediaInfo(
        duration=_video_duration(path, video, data.get("format", {})),
        width=width,
        height=height,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def _seconds(value: float) -> str:
    return f"{value:.9f}"


def render_edit_plan(plan: EditPlan, output_dir: Path | None = None) -> Path:
    """Render ordered source intervals to ``output_dir/reel.mp4`` atomically.

    Video is decoded and encoded at each exact seek rather than stream copied at
    keyframes. Playback order follows ``plan.windows`` (source order may differ).
    All output has 1280×720 H.264 video, 30 fps, stereo 48 kHz AAC audio, and
    ``faststart``. Sources without audio receive silence. Container duration may
    differ by at most one frame because video is quantized to 30 fps.
    """
    # Revalidate to catch callers mutating a model after construction.
    plan = EditPlan.model_validate(plan.model_dump())
    if len(plan.windows) > 64:
        raise MediaError("An edit can contain at most 64 source intervals.")
    source = plan.source_path.expanduser().resolve()
    info = probe_media(source)
    if any(window.end_seconds > info.duration + 0.001 for window in plan.windows):
        raise MediaError("A selected interval extends beyond the actual source video.")
    if any(window.end_seconds - window.start_seconds < 1 / 30 for window in plan.windows):
        raise MediaError("Each selected interval must include at least one video frame (1/30 s).")
    if output_dir is None:
        output_dir = Path(os.getenv("HIGHLIGHT_OUTPUT_DIR", "outputs")) / plan.id
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "reel.mp4"
    if destination == source:
        raise MediaError("Choose an output directory that will not overwrite the source video.")

    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    filters: list[str] = []
    labels: list[str] = []
    for index, window in enumerate(plan.windows):
        length = _seconds(window.end_seconds - window.start_seconds)
        command.extend(
            [
                "-threads",
                "2",
                "-ss",
                _seconds(window.start_seconds),
                "-t",
                length,
                "-i",
                str(source),
            ]
        )
        filters.append(
            f"[{index}:v:0]trim=duration={length},"
            "fps=30:start_time=0,scale=1280:720:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,format=yuv420p[v{index}]"
        )
        if info.has_audio:
            # Input seeking already rebases both streams to the clip origin.
            # Keep delayed audio timestamps so resampling fills leading silence.
            filters.append(
                f"[{index}:a:0]aresample=48000:async=1:first_pts=0,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"apad,atrim=duration={length}[a{index}]"
            )
        else:
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={length},asetpts=PTS-STARTPTS[a{index}]"
            )
        labels.append(f"[v{index}][a{index}]")
    filters.append("".join(labels) + f"concat=n={len(plan.windows)}:v=1:a=1[vout][aout]")
    command.extend(
        [
            "-filter_complex_threads",
            "2",
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            _seconds(plan.duration),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
        ]
    )
    with tempfile.TemporaryDirectory(prefix=".render-", dir=output_dir) as temporary:
        staged = Path(temporary) / "reel.mp4"
        _run(command + [str(staged)], timeout=min(2400, max(90, 30 + plan.duration * 10)))
        rendered = probe_media(staged)
        if abs(rendered.duration - plan.duration) > 0.08:
            raise MediaError("The rendered duration does not match the selected intervals.")
        if (rendered.width, rendered.height) != (1280, 720) or not rendered.has_audio:
            raise MediaError("The rendered video did not meet the playback format requirements.")
        os.replace(staged, destination)
    return destination


def preview_window(source: Path, start: float, end: float, output_dir: Path) -> Path:
    """Render the actual selected source footage using the same trim semantics."""
    info = probe_media(source)
    plan = EditPlan(
        id=f"preview-{uuid4().hex}",
        video_id="source-preview",
        source_path=source,
        source_duration=info.duration,
        prompt="Source evidence preview",
        windows=[EditWindow(moment_ids=["preview"], start_seconds=start, end_seconds=end)],
        max_duration=end - start,
    )
    return render_edit_plan(plan, output_dir)


def _mix_narration(video: Path, narration: Path, destination: Path, *, duration: float) -> None:
    """Mix narration at full volume over the rendered source at 18 percent."""
    audio_data = _probe_json(narration)
    if not any(s.get("codec_type") == "audio" for s in audio_data.get("streams", [])):
        raise MediaError("The narration file has no audio stream.")
    if _duration(audio_data) > duration + 0.05:
        raise MediaError("Narration is longer than the assembled demo; trim it or add a scene.")
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(video),
            "-i",
            str(narration),
            "-filter_complex",
            "[0:a:0]volume=0.18[bed];[1:a:0]aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,apad[voice];"
            "[bed][voice]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95:level=0[mix]",
            "-map",
            "0:v:0",
            "-map",
            "[mix]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-t",
            _seconds(duration),
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            str(destination),
        ],
        timeout=min(900, max(60, duration * 3)),
    )
