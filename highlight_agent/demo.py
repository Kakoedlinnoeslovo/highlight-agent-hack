"""Assemble a manually selected screen rehearsal with the highlight renderer.

Usage: python -m highlight_agent.demo manifest.json --output-dir outputs/demo
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from .media import MediaError, _mix_narration, probe_media, render_edit_plan
from .models import EditPlan, EditWindow


def _relative_file(value: object, base: Path, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a local file path.")
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def assemble_demo(manifest_path: Path, output_dir: Path) -> Path:
    """Render a rehearsal's ordered scenes, with optional human narration.

    Manifest shape::

        {"source_path": "rehearsal.mp4", "max_duration_seconds": 120,
         "scenes": [{"start_seconds": 5, "end_seconds": 12, "label": "Brief"}],
         "narration_path": "voice.wav"}

    Paths are relative to the manifest. Scene timestamps are source-relative;
    they must not overlap, but can be reordered. The duration ceiling defaults
    to 120 seconds and must be between 1 and 180 seconds. No media is downloaded
    and no scenes are selected automatically. Labels are retained in the saved
    EditPlan rather than burned into the video.
    """
    manifest_path = Path(manifest_path).expanduser().resolve()
    try:
        data = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read demo manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Demo manifest must be a JSON object.")
    source = _relative_file(data.get("source_path"), manifest_path.parent, "source_path")
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes or len(scenes) > 64:
        raise ValueError("Choose between 1 and 64 demo scenes.")
    maximum = data.get("max_duration_seconds", 120)
    if isinstance(maximum, bool) or not isinstance(maximum, (int, float)):
        raise ValueError("max_duration_seconds must be a number between 1 and 180.")
    if not math.isfinite(maximum) or not 1 <= maximum <= 180:
        raise ValueError("max_duration_seconds must be between 1 and 180.")
    windows = []
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise ValueError(f"Scene {index + 1} must be an object.")
        # Deliberately accept only the two timeline fields, never model-authored
        # timestamps or arbitrary FFmpeg arguments.
        try:
            windows.append(
                EditWindow(
                    moment_ids=[f"demo-scene-{index + 1}"],
                    start_seconds=scene["start_seconds"],
                    end_seconds=scene["end_seconds"],
                    caption=scene.get("label", f"Scene {index + 1}"),
                    reason="Manually selected from a genuine browser rehearsal.",
                )
            )
        except (KeyError, ValidationError) as exc:
            raise ValueError(f"Invalid scene {index + 1}: {exc}") from exc
    info = probe_media(source)
    plan = EditPlan(
        id=f"demo-{uuid4().hex}",
        video_id="demo-rehearsal",
        source_path=source,
        source_duration=info.duration,
        prompt="Manually assembled product demonstration",
        windows=windows,
        max_duration=maximum,
    )
    narration = None
    if data.get("narration_path") is not None:
        narration = _relative_file(data["narration_path"], manifest_path.parent, "narration_path")
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "demo.mp4"
    if destination == source or destination == narration:
        raise ValueError("The output directory would overwrite source media.")
    with tempfile.TemporaryDirectory(prefix=".demo-", dir=output_dir) as temporary:
        work = Path(temporary)
        reel = render_edit_plan(plan, work)
        staged = work / "demo.mp4"
        if narration is not None:
            _mix_narration(reel, narration, staged, duration=plan.duration)
        else:
            shutil.copyfile(reel, staged)
        rendered = probe_media(staged)
        if abs(rendered.duration - plan.duration) > 0.08:
            raise MediaError("The demo export duration does not match the selected scenes.")
        plan_file = work / "edit_plan.json"
        plan_file.write_text(plan.model_dump_json(indent=2) + "\n")
        os.replace(staged, destination)
        os.replace(plan_file, output_dir / "edit_plan.json")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/demo"))
    args = parser.parse_args()
    try:
        result = assemble_demo(args.manifest, args.output_dir)
    except (ValueError, MediaError) as exc:
        parser.exit(1, f"Demo export failed: {exc}\n")
    print(result)


if __name__ == "__main__":
    main()
