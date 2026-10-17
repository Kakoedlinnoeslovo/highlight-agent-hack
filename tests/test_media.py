"""Exercise real FFmpeg outputs, not mocked media command construction."""

from __future__ import annotations

import array
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from highlight_agent.demo import assemble_demo
from highlight_agent.media import MediaError, preview_window, probe_media, render_edit_plan
from highlight_agent.models import EditPlan, EditWindow

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg and ffprobe are required for real media integration checks.",
)


def ffmpeg(*arguments: str) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *arguments],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout


@pytest.fixture(scope="module")
def sources(tmp_path_factory) -> tuple[Path, Path, Path]:
    directory = tmp_path_factory.mktemp("media-sources")
    with_audio = directory / "three colors with audio.mp4"
    without_audio = directory / "three-colors-silent.mp4"
    narration = directory / "narration.wav"
    arguments = []
    for color in ("red", "green", "blue"):
        arguments.extend(["-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=30:d=2"])
    arguments.extend(["-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=6"])
    ffmpeg(
        *arguments,
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        "3:a",
        "-c:v",
        "libx264",
        "-g",
        "250",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(with_audio),
    )
    ffmpeg("-i", str(with_audio), "-map", "0:v", "-c:v", "copy", "-an", str(without_audio))
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=880:duration=1", str(narration))
    return with_audio, without_audio, narration


def make_plan(source: Path, windows: list[tuple[float, float]], maximum=30) -> EditPlan:
    return EditPlan(
        id="test-render",
        video_id="test-source",
        source_path=source,
        source_duration=probe_media(source).duration,
        prompt="Integration test",
        windows=[
            EditWindow(moment_ids=[f"m{i}"], start_seconds=start, end_seconds=end)
            for i, (start, end) in enumerate(windows)
        ],
        max_duration=maximum,
    )


def rgb_at(path: Path, seconds: float) -> tuple[int, int, int]:
    sample = ffmpeg(
        "-ss",
        str(seconds),
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        "scale=1:1",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    )
    assert len(sample) == 3
    return tuple(sample)


def audio_rms_at(path: Path, seconds: float) -> float:
    samples = array.array(
        "f",
        ffmpeg(
            "-ss", str(seconds), "-t", "0.2", "-i", str(path),
            "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1",
        ),
    )
    assert samples
    return math.sqrt(sum(value * value for value in samples) / len(samples))


def test_render_keeps_delayed_audio_aligned_across_reordered_windows(tmp_path):
    source = tmp_path / "delayed-audio.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=4",
        "-itsoffset", "2", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(source),
    )
    plan = make_plan(source, [(3, 4), (0.5, 2.5)], maximum=3)
    output = render_edit_plan(plan, tmp_path / "delayed-render")
    assert probe_media(output).duration == pytest.approx(3, abs=0.04)
    assert audio_rms_at(output, 0.25) > 0.03
    assert audio_rms_at(output, 1.25) < 0.002
    assert audio_rms_at(output, 2.7) > 0.03
    silent = render_edit_plan(make_plan(source, [(0, 1)]), tmp_path / "before-audio")
    assert probe_media(silent).duration == pytest.approx(1, abs=0.04)
    assert audio_rms_at(silent, 0.3) < 0.002


@pytest.mark.parametrize("extension", ["mp4", "mkv"])
def test_audio_tail_cannot_extend_usable_video_or_selected_intervals(tmp_path, extension):
    source = tmp_path / f"short-video.{extension}"
    ffmpeg(
        "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(source),
    )
    assert probe_media(source).duration == pytest.approx(2, abs=0.04)
    plan = EditPlan(
        id="stale-duration", video_id="test-source", source_path=source,
        source_duration=5, prompt="Stale source metadata",
        windows=[EditWindow(moment_ids=["tail"], start_seconds=1, end_seconds=4)],
    )
    with pytest.raises(MediaError, match="actual source video"):
        render_edit_plan(plan, tmp_path / "invalid-audio-tail")


@pytest.mark.parametrize("extension", ["mp4", "mkv"])
def test_positive_video_offset_keeps_source_timestamps_and_valid_end(tmp_path, extension):
    source = tmp_path / f"delayed-video.{extension}"
    ffmpeg(
        "-itsoffset", "2", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-fps_mode", "passthrough", "-c:a", "aac", str(source),
    )
    metadata = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(source)],
            capture_output=True, text=True, check=True, timeout=20,
        ).stdout
    )
    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
    assert float(video["start_time"]) == pytest.approx(2, abs=0.03)
    assert probe_media(source).duration == pytest.approx(4, abs=0.04)
    output = render_edit_plan(make_plan(source, [(2, 4)]), tmp_path / "offset-render")
    assert probe_media(output).duration == pytest.approx(2, abs=0.04)
    red, green, blue = rgb_at(output, 1.5)
    assert blue > 200 and red < 40 and green < 40
    assert audio_rms_at(output, 0.5) > 0.03


def test_probe_real_audio_and_silent_sources(sources):
    with_audio, without_audio, _ = sources
    first, second = probe_media(with_audio), probe_media(without_audio)
    assert first.has_audio and not second.has_audio
    assert (first.width, first.height) == (160, 90)
    assert first.duration == pytest.approx(6, abs=0.03)


def test_render_preserves_reordered_exact_seeks_and_playback_format(sources, tmp_path):
    source, _, _ = sources
    plan = make_plan(source, [(4.2, 5.4), (0.2, 1.5)], maximum=2.5)
    output = render_edit_plan(plan, tmp_path / "render")
    assert output.name == "reel.mp4"
    info = probe_media(output)
    assert info.has_audio and (info.width, info.height) == (1280, 720)
    assert info.duration == pytest.approx(2.5, abs=0.04)
    red, green, blue = rgb_at(output, 0.2)
    assert blue > 200 and red < 40 and green < 40
    red, green, blue = rgb_at(output, 1.5)
    assert red > 200 and green < 40 and blue < 40
    streams = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        ).stdout
    )["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert (video["codec_name"], video["pix_fmt"], video["r_frame_rate"]) == (
        "h264",
        "yuv420p",
        "30/1",
    )
    assert audio["codec_name"] == "aac"
    assert not list(output.parent.glob(".render-*"))


def test_preview_inserts_silence_for_missing_audio(sources, tmp_path):
    _, source, _ = sources
    output = preview_window(source, 2.1, 2.8, tmp_path / "preview")
    info = probe_media(output)
    assert info.has_audio and info.duration == pytest.approx(0.7, abs=0.04)
    red, green, blue = rgb_at(output, 0.2)
    assert green > 100 and red < 40 and blue < 40
    # Decode both streams to ensure the browser will receive a playable asset.
    ffmpeg("-i", str(output), "-f", "null", "-")


def test_demo_uses_same_renderer_and_preserves_full_duration_with_short_narration(
    sources, tmp_path
):
    source, _, narration = sources
    manifest = tmp_path / "demo.json"
    manifest.write_text(
        json.dumps(
            {
                "source_path": str(source),
                "max_duration_seconds": 120,
                "scenes": [
                    {"start_seconds": 4, "end_seconds": 5.5, "label": "Crash result"},
                    {"start_seconds": 0, "end_seconds": 1.5, "label": "Source evidence"},
                ],
                "narration_path": str(narration),
            }
        )
    )
    output = assemble_demo(manifest, tmp_path / "demo-export")
    assert output.name == "demo.mp4"
    assert probe_media(output).duration == pytest.approx(3, abs=0.04)
    assert probe_media(output).has_audio
    plan = EditPlan.model_validate_json((output.parent / "edit_plan.json").read_text())
    assert plan.windows[0].caption == "Crash result" and plan.duration == 3
    red, green, blue = rgb_at(output, 0.2)
    assert blue > 200 and red < 40
    ffmpeg("-i", str(output), "-f", "null", "-")


def test_invalid_edits_fail_without_replacing_previous_output(sources, tmp_path):
    source, _, narration = sources
    with pytest.raises(ValueError):
        make_plan(source, [(0, 2), (3, 5)], maximum=3)
    output_dir = tmp_path / "demo-output"
    output_dir.mkdir()
    previous = output_dir / "demo.mp4"
    previous.write_bytes(b"previous successful output")
    manifest = tmp_path / "invalid-demo.json"
    manifest.write_text(
        json.dumps(
            {
                "source_path": str(source),
                "scenes": [{"start_seconds": 0, "end_seconds": 0.5}],
                "narration_path": str(narration),
            }
        )
    )
    with pytest.raises(MediaError, match="Narration is longer"):
        assemble_demo(manifest, output_dir)
    assert previous.read_bytes() == b"previous successful output"
    assert not list(output_dir.glob(".demo-*"))


def test_missing_source_and_out_of_range_actual_media_are_reported(sources, tmp_path):
    with pytest.raises(MediaError, match="does not exist"):
        probe_media(tmp_path / "missing.mp4")
    source, _, _ = sources
    plan = make_plan(source, [(0, 1)])
    plan.source_duration = 10
    plan.windows[0].start_seconds = 7
    plan.windows[0].end_seconds = 8
    with pytest.raises(MediaError, match="actual source"):
        render_edit_plan(plan, tmp_path / "invalid")


def test_demo_duration_ceiling_and_relative_paths(sources, tmp_path):
    source, _, _ = sources
    local = tmp_path / "recording.mp4"
    shutil.copyfile(source, local)
    manifest = tmp_path / "relative.json"
    data = {"source_path": "recording.mp4", "scenes": [{"start_seconds": 0, "end_seconds": 1}]}
    data["max_duration_seconds"] = 181
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="between 1 and 180"):
        assemble_demo(manifest, tmp_path / "bad")
    data["max_duration_seconds"] = 120
    manifest.write_text(json.dumps(data))
    output = assemble_demo(manifest, tmp_path / "relative-output")
    assert probe_media(output).duration == pytest.approx(1, abs=0.04)
