"""Command line entry points for setup, reproducible edits, and demo exports."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

from .config import Settings
from .demo import assemble_demo
from .evaluation import evaluate_manifest
from .inference import InferenceError
from .media import MediaError
from .models import Record
from .service import HighlightService, ServiceError
from .storage import write_json
from .vast import ProviderError


def _emit(value):
    if isinstance(value, BaseModel):
        print(value.model_dump_json(indent=2))
    else:
        print(json.dumps(value, indent=2, default=str, allow_nan=False))


def doctor(service: HighlightService, remote=False) -> dict:
    report = service.status()
    report["tools"] = {tool: bool(shutil.which(tool)) for tool in ("ffmpeg", "ffprobe")}
    if report["tools"]["ffmpeg"]:
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-filters"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            names = {
                line.split()[1] for line in result.stdout.splitlines() if len(line.split()) >= 2
            }
            report["optional_filters"] = {name: name in names for name in ("drawtext", "subtitles")}
        except (OSError, subprocess.SubprocessError):
            report["optional_filters"] = {"status": "could not inspect"}
    if remote and service.config.mode == "live":
        report["remote"] = {}
        try:
            report["remote"]["VAST"] = service._vast_client().doctor()
        except (ServiceError, ProviderError) as error:
            report["remote"]["VAST"] = {"error": str(error)}
        try:

            class Ping(Record):
                ok: bool

            service.tracer.initialize()
            with service.tracer.span(
                "doctor.inference", {"model": service.config.wandb_model}
            ) as span:
                ping = service._inference_client().structured(
                    "Return a JSON object with ok=true.", {}, Ping
                )
                span["output"] = ping.model_dump()
            report["remote"]["W&B Inference"] = {"ok": ping.ok, "trace_url": span["url"]}
            if service.tracer.warning:
                report["remote"]["Weave"] = {"warning": service.tracer.warning}
        except (ServiceError, InferenceError) as error:
            report["remote"]["W&B Inference"] = {"error": str(error)}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Highlight Agent engine: sources, indexing, edits, and demo assembly. "
        "Corner Scout lives in scripts/corner_scout.py and web/."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    health = commands.add_parser(
        "doctor", help="Inspect local configuration; --remote tests real APIs"
    )
    health.add_argument("--remote", action="store_true")
    register = commands.add_parser("register", help="Copy and register an exact local source video")
    register.add_argument("path", type=Path)
    register.add_argument("--label", default="")
    register.add_argument("--vast-source", default=None)
    for name in ("ingest", "refresh"):
        commands.add_parser(name).add_argument("video_id")
    commands.add_parser("list", help="List registered sources for the current mode")
    commands.add_parser("datasets", help="Browse all supplied public recordings")
    dataset = commands.add_parser("import-dataset", help="Import one complete supplied recording")
    dataset.add_argument("source_uri")
    datasets = commands.add_parser("import-datasets", help="Resume importing the complete public collection")
    datasets.add_argument("--all", action="store_true", required=True)
    datasets.add_argument("--keep-originals", action="store_true",
                          help="Keep verified raw downloads locally after complete imports")
    commands.add_parser("sample", help="Create an explicitly synthetic offline sample")
    make = commands.add_parser("make", help="Search, select, and render a fresh highlight")
    make.add_argument("video_id")
    make.add_argument("--prompt", required=True)
    make.add_argument("--duration", type=float, default=30)
    revise = commands.add_parser(
        "revise", help="Remove a visible clip and/or put the highest-ranked first"
    )
    revise.add_argument("edit_id")
    revise.add_argument("--instruction", required=True)
    commands.add_parser("undo", help="Load the previous immutable edit").add_argument("edit_id")
    demo = commands.add_parser("demo", help="Assemble a genuine rehearsal with the shared renderer")
    demo.add_argument("manifest", type=Path)
    demo.add_argument("--output-dir", type=Path, default=Path("outputs/demo"))
    evaluation = commands.add_parser(
        "evaluate", help="Review indexed captions against six labeled clips"
    )
    evaluation.add_argument("manifest", type=Path)
    evaluation.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            _emit({"output_path": str(assemble_demo(args.manifest, args.output_dir))})
            return 0
        service = HighlightService(Settings.from_env())

        def progress(stage):
            print(stage, file=sys.stderr)

        if args.command == "doctor":
            report = doctor(service, args.remote)
            _emit(report)
            remote_failed = any(
                item.get("error") or item.get("errors")
                for item in report.get("remote", {}).values()
            )
            return int(
                bool(report["missing"]) or not all(report["tools"].values()) or bool(remote_failed)
            )
        if args.command == "register":
            _emit(service.register_video(args.path, args.label, args.vast_source))
        elif args.command == "list":
            _emit([video.model_dump(mode="json") for video in service.list_videos()])
        elif args.command == "datasets":
            _emit(service.dataset_sources())
        elif args.command == "import-dataset":
            _emit(service.import_dataset_source(args.source_uri))
        elif args.command == "import-datasets":
            report = service.import_datasets(progress, keep_originals=args.keep_originals)
            _emit(report)
            return 0 if report["complete"] else 1
        elif args.command == "sample":
            _emit(service.register_sample())
        elif args.command == "ingest":
            _emit(service.ingest_video(args.video_id))
        elif args.command == "refresh":
            _emit(service.refresh_index(args.video_id))
        elif args.command == "make":
            _emit(service.make_highlight(args.video_id, args.prompt, args.duration, progress))
        elif args.command == "revise":
            _emit(service.revise_edit(args.edit_id, args.instruction, progress))
        elif args.command == "undo":
            _emit(service.undo_edit(args.edit_id))
        elif args.command == "evaluate":
            report = evaluate_manifest(args.manifest, service)
            if args.output:
                write_json(args.output, report)
            _emit(report)
            return {"passed": 0, "failed": 1, "needs_review": 2}[report["status"]]
        return 0
    except (ServiceError, ProviderError, InferenceError, MediaError, ValueError, OSError) as error:
        print(f"Highlight Agent: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
