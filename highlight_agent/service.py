"""One application service for the UI, CLI, genuine provider runs, and labeled fixtures."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .inference import InferenceClient
from .media import preview_window, probe_media, render_edit_plan
from .models import EditPlan, EditWindow, HighlightResult, Moment, SourceChunk, VideoRecord
from .selection import SelectionError, create_plan, revise_plan, select_windows
from .storage import read_json, write_json
from .tracing import Tracer


class ServiceError(RuntimeError):
    pass


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ServiceError("Invalid video or edit ID.")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class HighlightService:
    def __init__(
        self,
        config: Settings | None = None,
        *,
        vast_client=None,
        inference_client=None,
        renderer=render_edit_plan,
    ):
        config = config or Settings.from_env()
        self.config = replace(
            config, data_dir=config.data_dir.resolve(), output_dir=config.output_dir.resolve()
        )
        self._vast = vast_client
        self._inference = inference_client
        self._renderer = renderer
        self.tracer = Tracer(self.config)
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict:
        return {
            "mode": self.config.mode,
            "missing": self.config.missing(),
            "providers": {
                "VAST": "configured" if self.config.vast_base_url else "not configured",
                "Cosmos": "expected: " + self.config.vast_caption_model
                if self.config.vast_caption_model
                else "not verified against a deployment",
                "W&B Inference": "configured"
                if self.config.wandb_api_key and self.config.wandb_model
                else "not configured",
                "Weave": "configured"
                if self.config.wandb_api_key and self.config.wandb_project
                else "not configured",
                "FFmpeg": "available" if shutil.which("ffmpeg") else "missing",
            },
        }

    def _vast_client(self):
        if self._vast is None:
            if not self.config.vast_base_url or not (
                self.config.vast_access_token
                or (self.config.vast_username and self.config.vast_password)
            ):
                raise ServiceError("Configure VAST_BASE_URL and VAST credentials in .env first.")
            from .vast import VASTClient

            self._vast = VASTClient(self.config)
        return self._vast

    def _inference_client(self):
        if self._inference is None:
            if not all(
                (self.config.wandb_api_key, self.config.wandb_model, self.config.wandb_project)
            ):
                raise ServiceError("Configure WANDB_API_KEY, WANDB_PROJECT, and WANDB_MODEL first.")
            self._inference = InferenceClient(self.config)
        return self._inference

    def _video_path(self, video_id: str) -> Path:
        return self.config.data_dir / "videos" / f"{_safe_id(video_id)}.json"

    def _save_video(self, video: VideoRecord) -> VideoRecord:
        write_json(self._video_path(video.id), video)
        return video

    @staticmethod
    def _verify_source(video: VideoRecord) -> None:
        if not video.local_path.is_file():
            raise ServiceError(
                "The registered source is missing. Restore it or register the recording again."
            )
        if _sha256(video.local_path) != video.content_sha256:
            raise ServiceError(
                "The registered source file changed. Register the exact matching media again."
            )

    def get_video(self, video_id: str) -> VideoRecord:
        path = self._video_path(video_id)
        if not path.exists():
            raise ServiceError("Video not found. Register a local recording first.")
        video = VideoRecord.model_validate(read_json(path))
        if video.mode != self.config.mode:
            raise ServiceError(
                "This video belongs to a different mode. Fixtures cannot become live evidence."
            )
        return video

    def list_videos(self) -> list[VideoRecord]:
        records = []
        for path in sorted((self.config.data_dir / "videos").glob("*.json")):
            record = VideoRecord.model_validate(read_json(path))
            if record.mode == self.config.mode:
                records.append(record)
        return records

    def register_video(self, path: Path, label: str = "", original_video: str | None = None):
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise ServiceError("Choose an existing local video file.")
        if original_video and not re.fullmatch(r"s3://[^/\s]+/.+", original_video):
            raise ServiceError("An existing VAST source must be its exact s3://bucket/key URI.")
        info = probe_media(path)
        fingerprint = _sha256(path)
        for record in self.list_videos():
            if record.content_sha256 == fingerprint and record.original_video == original_video:
                if not record.local_path.is_file() or _sha256(record.local_path) != fingerprint:
                    record.local_path.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(
                        prefix=".restore-", dir=record.local_path.parent
                    ) as temporary:
                        restored = Path(temporary) / "source"
                        shutil.copyfile(path, restored)
                        if _sha256(restored) != fingerprint:
                            raise ServiceError(
                                "The source file changed while restoring it. Retry registration."
                            )
                        restored.replace(record.local_path)
                return record
        video_id = uuid4().hex
        target = self.config.data_dir / "media" / f"{video_id}{path.suffix.lower()}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        video = VideoRecord(
            id=video_id,
            label=(label.strip() or path.stem)[:120],
            local_path=target,
            duration=info.duration,
            original_video=original_video,
            index_status="indexing" if original_video else "local",
            content_sha256=fingerprint,
            mode=self.config.mode,
        )
        return self._save_video(video)

    def dataset_sources(self) -> list[dict]:
        if self.config.mode != "live":
            raise ServiceError("The supplied public dataset requires live mode.")
        return self._vast_client().dataset_sources()

    @staticmethod
    def dataset_import_signature(source: dict) -> str:
        """An immutable identity for one ordered, indexed source membership."""
        membership = {
            key: source.get(key) for key in ("stream_id", "camera_id", "location", "capture_type")
        }
        membership["chunks"] = [
            {key: chunk.get(key) for key in
             ("original_video", "duration", "chunk_index", "segment_count")}
            for chunk in source["chunks"]
        ]
        return hashlib.sha256(json.dumps(membership, sort_keys=True).encode()).hexdigest()

    @contextmanager
    def _dataset_import_lock(self):
        """Keep shared download/preparation checkpoints alive across CLI and UI imports."""
        import fcntl

        path = self.config.data_dir / "catalog" / "import.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _dataset_totals(recordings: list[dict]) -> dict:
        return {"recordings": len(recordings),
                "chunks": sum(len(item["chunks"]) for item in recordings),
                "segments": sum(item.get("segment_count", 0) for item in recordings),
                "duration_seconds": sum(sum(chunk["duration"] for chunk in item["chunks"])
                                        for item in recordings)}

    def _save_dataset_inventory(self, sources: list[dict]) -> tuple[dict, list[dict]]:
        """Publish one full discovery snapshot under the catalog import lock."""
        catalog_dir = self.config.data_dir / "catalog"
        inventory = {"dataset": "hackathon-public", "sources": sources, "recordings": sources,
                     "chunks": getattr(self._vast_client(), "dataset_inventory", []),
                     "captured_at": datetime.now(UTC).isoformat(),
                     "baseline": {"chunks": 612, "recordings": 55, "segments": 3537}}
        originals = [chunk["original_video"] for source in sources for chunk in source["chunks"]]
        if len(originals) != len(set(originals)):
            raise ServiceError("A supplied chunk appears in more than one recording.")
        expected = self._dataset_totals(sources)
        baseline_path = self.config.output_dir / "validation" / "public-dataset-inventory.json"
        baseline_chunks = read_json(baseline_path).get("chunks", []) if baseline_path.exists() else []
        discovered_originals = set(originals)
        missing = [chunk for chunk in baseline_chunks if chunk["original_video"] not in discovered_originals]
        expected["chunks"] += len(missing)
        expected["segments"] += sum(chunk["total_segments"] for chunk in missing)
        expected["duration_seconds"] += sum(chunk["chunk_duration_sec"] for chunk in missing)
        discovered_streams = {source.get("stream_id") for source in sources}
        expected["recordings"] += len({chunk["stream_id"] for chunk in missing} - discovered_streams)
        inventory["missing_sources"] = [chunk["original_video"] for chunk in missing]
        inventory["expected"] = expected
        write_json(catalog_dir / "inventory.json", inventory)
        return inventory, missing

    def _prepare_dataset_chunk(self, chunk: dict, video_id: str,
                               previous: SourceChunk | None = None) -> tuple[Path, SourceChunk]:
        """Keep verified download and normalization checkpoints across interrupted imports."""
        uri = chunk["original_video"]
        cache = self.config.data_dir / "catalog" / "chunks" / hashlib.sha256(uri.encode()).hexdigest()
        cache.mkdir(parents=True, exist_ok=True)
        raw = cache / "source.mp4"
        metadata_path = cache / "source.json"
        metadata = read_json(metadata_path) if metadata_path.exists() else {}
        valid = (raw.is_file() and metadata.get("original_video") == uri
                 and _sha256(raw) == metadata.get("source_sha256"))
        if not valid:
            self._vast_client().download_dataset_source(uri, raw)
            fingerprint = _sha256(raw)
            expected_hash = previous.source_sha256 if previous else metadata.get("source_sha256")
            if expected_hash and fingerprint != expected_hash:
                raise ServiceError("The supplied dataset object changed. Restore the matching media before retrying.")
            info = probe_media(raw)
            metadata = {"original_video": uri, "source_sha256": fingerprint,
                        "source_duration": info.duration}
            write_json(metadata_path, metadata)
        if previous and previous.source_sha256 and metadata["source_sha256"] != previous.source_sha256:
            raise ServiceError("The supplied dataset object changed. Restore the matching media before retrying.")
        source_duration = metadata["source_duration"]
        if chunk["duration"] > source_duration + 0.1:
            raise ServiceError(
                f"Dataset media duration does not match indexed coverage: source measures "
                f"{source_duration:.3f}s; its index requires {chunk['duration']:.3f}s."
            )
        included = min(source_duration, chunk["duration"])
        normalization = hashlib.sha256(
            f"{metadata['source_sha256']}|{included:.9f}|720p30-v1".encode()
        ).hexdigest()
        normalized = cache / f"{normalization}.mp4"
        normalized_metadata = cache / f"{normalization}.json"
        checkpoint = read_json(normalized_metadata) if normalized_metadata.exists() else {}
        if not (normalized.is_file() and _sha256(normalized) == checkpoint.get("sha256")):
            with tempfile.TemporaryDirectory(prefix=".normalize-", dir=cache) as folder:
                plan = EditPlan(
                    id="dataset-chunk", video_id=video_id, source_path=raw,
                    source_duration=source_duration, prompt="Prepare the supplied indexed recording.",
                    windows=[EditWindow(moment_ids=["chunk"], start_seconds=0, end_seconds=included)],
                    max_duration=max(30, included),
                )
                staged = render_edit_plan(plan, Path(folder))
                measured = probe_media(staged).duration
                checkpoint = {"sha256": _sha256(staged), "duration": measured,
                              "source_sha256": metadata["source_sha256"]}
                staged.replace(normalized)
                write_json(normalized_metadata, checkpoint)
        measured = checkpoint["duration"]
        if abs(measured - included) > 0.08:
            raise ServiceError("The normalized dataset timing does not match indexed coverage.")
        if previous and abs(measured - previous.duration) > 0.001:
            raise ServiceError("The rebuilt dataset timing does not match the registered recording.")
        return normalized, SourceChunk(
            original_video=uri, start_seconds=0, duration=measured,
            source_duration=source_duration, source_sha256=metadata["source_sha256"],
            chunk_index=chunk.get("chunk_index"), segment_count=chunk.get("segment_count"),
        )

    def import_dataset_source(self, original_video: str, source: dict | None = None) -> VideoRecord:
        """Import one recording without exposing shared checkpoint races.

        ``source`` is a bulk optimization: its complete discovery snapshot must
        already be saved. UI and CLI single imports omit it to refresh inventory.
        """
        if self.config.mode != "live":
            raise ServiceError("The supplied public dataset requires live mode.")
        with self._dataset_import_lock():
            return self._import_dataset_source(original_video, source)

    def _import_dataset_source(self, original_video: str, source: dict | None = None) -> VideoRecord:
        """The caller holds the process-safe catalog lock through staging and cleanup."""
        if self.config.mode != "live":
            raise ServiceError("The supplied public dataset requires live mode.")
        discovered = None
        if source is None:
            discovered = self.dataset_sources()
            source = next((item for item in discovered
                           if item["original_video"] == original_video), None)
        if source is None or source.get("original_video") != original_video or not source.get("chunks"):
            raise ServiceError("Choose a recording from the supplied public dataset.")
        signature = self.dataset_import_signature(source)
        cached = next((video for video in self.list_videos()
                       if video.dataset == "hackathon-public" and video.import_signature == signature), None)
        if cached:
            try:
                self._verify_source(cached)
                rows = self.indexed_segments(cached.id)
                if self._with_index_status(cached, rows).index_status == "ready":
                    if discovered is not None:
                        self._save_dataset_inventory(discovered)
                    return cached
            except ServiceError:
                pass
        media_dir = self.config.data_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        video_id = cached.id if cached else "public-" + signature[:32]
        destination = cached.local_path if cached else media_dir / f"{video_id}.mp4"
        previous = {chunk.original_video: chunk for chunk in cached.source_chunks} if cached else {}
        # The shared httpx client is thread-safe. Authenticate before worker requests.
        provider = self._vast_client()
        if hasattr(provider, "_login") and not provider._token:
            provider._login()
        def prepare(chunk):
            return self._prepare_dataset_chunk(chunk, video_id, previous.get(chunk["original_video"]))
        with ThreadPoolExecutor(max_workers=3) as executor:
            prepared = list(executor.map(prepare, source["chunks"]))
        chunks = []
        offset = 0.0
        with tempfile.TemporaryDirectory(prefix=".public-import-", dir=media_dir) as folder:
            for index, (path, chunk) in enumerate(prepared):
                chunks.append(chunk.model_copy(update={"start_seconds": offset}))
                offset += chunk.duration
                # Hard links retain generated safe names without duplicating media bytes.
                (Path(folder) / f"prepared-{index}.mp4").hardlink_to(path)
            manifest = Path(folder) / "concat.txt"
            manifest.write_text("".join(f"file 'prepared-{index}.mp4'\n" for index in range(len(chunks))))
            assembled = Path(folder) / "assembled.mp4"
            try:
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                    "-safe", "1", "-i", str(manifest), "-map", "0:v:0", "-map", "0:a?",
                    "-c", "copy", "-movflags", "+faststart", str(assembled),
                ], check=True, capture_output=True, timeout=180)
            except (OSError, subprocess.SubprocessError):
                raise ServiceError("Could not assemble the dataset recording. Check FFmpeg and retry.") from None
            if abs(probe_media(assembled).duration - offset) > 0.1:
                raise ServiceError("Assembled dataset timestamps do not match its source chunks.")
            video = VideoRecord(
                id=video_id, label=source["label"], local_path=destination, duration=offset,
                original_video=original_video, index_status="indexing", dataset="hackathon-public",
                source_chunks=chunks, content_sha256=_sha256(assembled), mode="live",
                import_signature=signature,
                **{key: source.get(key) for key in ("stream_id", "camera_id", "location", "capture_type")},
            )
            rows = provider.segments(video)
            video = self._with_index_status(video, rows)
            if video.index_status != "ready":
                raise ServiceError("The dataset recording is not completely indexed. Retry the import later.")
            expected = source.get("segment_count")
            if expected is not None and len(rows) != expected:
                raise ServiceError("The dataset recording caption count does not match its inventory.")
            assembled.replace(destination)
        write_json(self._index_path(video.id), rows)
        saved = self._save_video(video)
        if discovered is not None:
            self._save_dataset_inventory(discovered)
        # The complete recording and caption index are durable now. Original
        # downloads remain recoverable; redundant prepared chunks can be rebuilt.
        for normalized, _ in prepared:
            normalized.unlink(missing_ok=True)
            normalized.with_suffix(".json").unlink(missing_ok=True)
        return saved

    def release_dataset_originals(self, video: VideoRecord) -> dict:
        """Serialize raw cleanup with imports that may still need those checkpoints."""
        with self._dataset_import_lock():
            return self._release_dataset_originals(video)

    def _release_dataset_originals(self, video: VideoRecord) -> dict:
        """Release verified raw cache copies once the indexed recording is durable.

        VAST retains the originals. Local hash metadata remains an integrity
        anchor for any future download; incomplete imports never reach here.
        """
        if video.dataset != "hackathon-public" or not video.source_chunks:
            raise ServiceError("Only complete supplied recordings can release cached originals.")
        persisted = self.get_video(video.id)
        if persisted != video:
            raise ServiceError("Persist the complete recording before releasing its originals.")
        self._verify_source(video)
        rows = self.indexed_segments(video.id)
        try:
            intervals = [(float(row["start_seconds"]), float(row["end_seconds"])) for row in rows]
        except (KeyError, TypeError, ValueError):
            raise ServiceError("Verify the caption intervals before releasing original downloads.") from None
        if any(not (math.isfinite(start) and math.isfinite(end)
                    and 0 <= start < end <= video.duration + 0.05) for start, end in intervals):
            raise ServiceError("Verify the caption intervals before releasing original downloads.")
        if video.index_status != "ready" or self._with_index_status(video, rows).index_status != "ready":
            raise ServiceError("Complete the caption index before releasing original downloads.")
        verified = []
        for chunk in video.source_chunks:
            cache = (self.config.data_dir / "catalog" / "chunks"
                     / hashlib.sha256(chunk.original_video.encode()).hexdigest())
            raw, metadata_path = cache / "source.mp4", cache / "source.json"
            if not raw.exists():
                continue
            if not metadata_path.is_file() or not chunk.source_sha256:
                raise ServiceError("Original cache integrity metadata is missing; no originals were released.")
            metadata = read_json(metadata_path)
            if (not isinstance(metadata, dict) or metadata.get("original_video") != chunk.original_video
                    or metadata.get("source_sha256") != chunk.source_sha256):
                raise ServiceError("Original cache integrity verification failed; no originals were released.")
            try:
                fingerprint, size = _sha256(raw), raw.stat().st_size
            except FileNotFoundError:
                # Another verified release may have removed this same raw copy.
                continue
            if fingerprint != chunk.source_sha256:
                raise ServiceError("Original cache integrity verification failed; no originals were released.")
            verified.append((raw, size))
        for raw, _ in verified:
            raw.unlink(missing_ok=True)
        return {"video_id": video.id, "released_chunks": len(verified),
                "released_bytes": sum(size for _, size in verified)}

    def import_datasets(self, progress: Callable[[str], None] | None = None,
                        *, keep_originals: bool = False) -> dict:
        """Discover once, checkpoint coverage after each recording, and retry unfinished imports."""
        from .media import MediaError
        from .vast import ProviderError

        with self._dataset_import_lock():
            sources = self.dataset_sources()
            inventory, missing = self._save_dataset_inventory(sources)
        expected = inventory["expected"]
        catalog_dir = self.config.data_dir / "catalog"
        imported = []
        playable = []
        failures = [{"stream_id": chunk.get("stream_id"),
                     "original_video": chunk["original_video"],
                     "error": "The saved public source is missing from the current indexed collection."}
                    for chunk in missing]
        report = {}
        def save():
            nonlocal report
            measured = self._dataset_totals(imported)
            measured["duration_seconds"] = sum(video.duration for _, video in completed)
            playback = self._dataset_totals(playable)
            playback["duration_seconds"] = measured["duration_seconds"]
            report = {"dataset": "hackathon-public", "expected": expected,
                      "imported": measured, "playable": playback,
                      "complete": bool(sources) and len(imported) == len(sources) and not failures,
                      "missing_sources": inventory["missing_sources"],
                      "original_storage": "local" if keep_originals else "VAST",
                      "released_originals": dict(released),
                      "failures": failures,
                      "baseline": inventory["baseline"],
                      "baseline_matches": all(expected[key] == value
                                              for key, value in inventory["baseline"].items()),
                      "video_ids": [video.id for _, video in completed]}
            write_json(catalog_dir / "coverage.json", report)
        completed = []
        released = {"chunks": 0, "bytes": 0}
        save()
        for index, source in enumerate(sources):
            if progress:
                progress(f"Importing {index + 1}/{len(sources)}: {source['label']}")
            try:
                video = self.import_dataset_source(source["original_video"], source=source)
                imported.append(source)
                playable.append(source)
                completed.append((source, video))
                if not keep_originals:
                    cleanup = self.release_dataset_originals(video)
                    released["chunks"] += cleanup["released_chunks"]
                    released["bytes"] += cleanup["released_bytes"]
            except (ProviderError, ServiceError, MediaError, OSError, ValueError) as error:
                failures.append({"stream_id": source.get("stream_id"),
                                 "original_video": source["original_video"], "error": str(error)})
            save()
        return report

    def ingest_video(self, video_id: str) -> VideoRecord:
        video = self.get_video(video_id)
        if self.config.mode != "live":
            raise ServiceError(
                "Synthetic sample mode never uploads footage or calls sponsor services."
            )
        if video.original_video:
            return self.refresh_index(video_id)
        self._verify_source(video)
        original_video = self._vast_client().upload(video)
        video = video.model_copy(
            update={"original_video": original_video, "index_status": "indexing"}
        )
        return self._save_video(video)

    def _index_path(self, video_id: str) -> Path:
        return self.config.data_dir / "index" / f"{_safe_id(video_id)}.json"

    def indexed_segments(self, video_id: str) -> list[dict]:
        path = self._index_path(video_id)
        return read_json(path) if path.exists() else []

    def refresh_index(self, video_id: str) -> VideoRecord:
        video = self.get_video(video_id)
        if self.config.mode == "offline_sample":
            return video
        if not video.original_video:
            raise ServiceError(
                "Upload the recording or register an existing VAST source URI first."
            )
        rows = self._vast_client().segments(video)
        write_json(self._index_path(video.id), rows)
        return self._save_video(self._with_index_status(video, rows))

    @staticmethod
    def _with_index_status(video: VideoRecord, rows: list[dict]) -> VideoRecord:
        windows = sorted((float(row["start_seconds"]), float(row["end_seconds"])) for row in rows)
        full_dataset = video.dataset == "hackathon-public"
        tolerance = 0.08 if full_dataset else 0.5
        valid_intervals = not full_dataset or all(
            math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= video.duration + 0.05
            for start, end in windows
        )
        coverage = 0.0
        gap = False
        for start, end in windows:
            if start > coverage + tolerance or (full_dataset and start < coverage - tolerance):
                gap = True
            coverage = max(coverage, end)
        totals = {int(row["total_segments"]) for row in rows if row.get("total_segments")}
        numbers = {
            int(row["segment_number"]) for row in rows if row.get("segment_number") is not None
        }
        declared_complete = True
        if totals:
            count = max(totals)
            first = 0 if 0 in numbers else 1
            declared_complete = len(totals) == 1 and numbers == set(range(first, first + count))
        expected_counts = [chunk.segment_count for chunk in video.source_chunks]
        if full_dataset and expected_counts and all(count is not None for count in expected_counts):
            declared_complete = declared_complete and len(rows) == sum(expected_counts)
        complete = (bool(rows) and valid_intervals and declared_complete and not gap
                    and coverage >= video.duration - tolerance)
        reported = sorted({str(row["cosmos_model"]) for row in rows if row.get("cosmos_model")})
        video = video.model_copy(
            update={
                "index_status": "ready" if complete else "indexing",
                "caption_model": ", ".join(reported) or None,
                "error": None,
            }
        )
        return video

    def _run_stage(self, name, action, inputs, parent, timings, progress):
        progress(name.replace("_", " "))
        started = time.perf_counter()
        with self.tracer.span(name, inputs, parent=parent) as span:
            result = action()
            if hasattr(result, "model_dump"):
                span["output"] = result.model_dump(mode="json")
            elif isinstance(result, list):
                span["output"] = [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in result
                ]
            elif isinstance(result, Path):
                span["output"] = {"file": result.name}
            else:
                span["output"] = result
        timings[name] = round(time.perf_counter() - started, 3)
        return result

    def make_highlight(
        self,
        video_id: str,
        prompt: str,
        max_duration=30,
        progress: Callable[[str], None] | None = None,
    ) -> HighlightResult:
        started = time.perf_counter()
        progress = progress or (lambda _: None)
        prompt = prompt.strip()
        if not 3 <= len(prompt) <= 2000:
            raise ServiceError("Describe the visual moments you want in 3–2000 characters.")
        if not 0 < max_duration <= 30:
            raise ServiceError("Choose a highlight duration between 1 and 30 seconds.")
        video = self.get_video(video_id)
        if video.index_status != "ready":
            raise ServiceError("This recording is not fully indexed yet. Refresh its index first.")
        self._verify_source(video)
        if self.config.mode == "live" and self.config.missing():
            raise ServiceError("Missing configuration: " + ", ".join(self.config.missing()))
        self.tracer.initialize()
        timings = {}
        warnings = []
        retrieved: dict[str, Moment] = {}
        queries = []
        with self.tracer.span(
            "highlight.make", {"video_id": video.id, "prompt": prompt, "max_duration": max_duration}
        ) as root:
            parent = root["call"]
            if self.config.mode == "offline_sample":
                progress("Selecting manually labeled synthetic fixtures — no sponsor calls")
                selected = self._sample_selection(video, prompt)
                retrieved = {item.id: item for item in selected}
                warnings.append(
                    "SYNTHETIC SAMPLE: manually labeled color panels; no gameplay analysis, "
                    "VAST, Cosmos, W&B Inference, or Weave calls were performed."
                )
            else:
                client = self._inference_client()
                brief = self._run_stage(
                    "translate_brief",
                    lambda: client.expand(prompt),
                    {"brief": prompt},
                    parent,
                    timings,
                    progress,
                )
                selected = []
                for index, query in enumerate([brief.query, brief.alternate_query], start=1):
                    if query in queries:
                        continue
                    queries.append(query)
                    matches = self._run_stage(
                        f"search_{index}",
                        lambda query=query: self._vast_client().search(video, query),
                        {"video_id": video.id, "query": query},
                        parent,
                        timings,
                        progress,
                    )
                    retrieved.update({item.id: item for item in matches})
                    models_for_source = getattr(self._vast_client(), "models_for_source", None)
                    reported = (
                        sorted(models_for_source(video.original_video)) if models_for_source else []
                    )
                    if reported and not video.caption_model:
                        video = self._save_video(
                            video.model_copy(update={"caption_model": ", ".join(reported)})
                        )
                    if retrieved:
                        selected = self._run_stage(
                            f"rank_{index}",
                            lambda: client.rank(prompt, list(retrieved.values())),
                            {"brief": prompt, "candidate_ids": list(retrieved)},
                            parent,
                            timings,
                            progress,
                        )
                    if len(selected) >= 3:
                        try:
                            select_windows(video, selected, max_duration)
                        except SelectionError:
                            continue
                        break
                warnings.append(
                    "Selections use indexed video captions; candidates were not freshly rewatched."
                )
                if not video.caption_model:
                    warnings.append(
                        "The index has not reported its executed caption model; Cosmos attribution "
                        "still needs deployment evidence."
                    )
            if not selected:
                raise SelectionError(
                    "No caption-supported matches were found. Try a different brief or footage."
                )
            plan = create_plan(video, prompt, selected, max_duration)
            output_dir = self.config.output_dir / plan.id
            output_path = self._run_stage(
                "render",
                lambda: self._renderer(plan, output_dir),
                {"edit_plan": plan.model_dump(mode="json")},
                parent,
                timings,
                progress,
            )
            actual_duration = probe_media(output_path).duration
            timings["total"] = round(time.perf_counter() - started, 3)
            selected_ids = {item for window in plan.windows for item in window.moment_ids}
            result = HighlightResult(
                edit_plan=plan,
                output_path=output_path,
                actual_duration=actual_duration,
                moments=[item for item in selected if item.id in selected_ids],
                timings=timings,
                trace_url=root["url"],
                warnings=warnings,
                mode=self.config.mode,
            )
            root["output"] = {
                "edit_id": plan.id,
                "duration": plan.duration,
                "selected_ids": sorted(selected_ids),
                "timings": timings,
            }
        if self.tracer.warning:
            result.warnings.append(self.tracer.warning)
        write_json(output_dir / "result.json", result)
        write_json(output_dir / "edit_plan.json", plan)
        write_json(
            output_dir / "evidence.json",
            {
                "mode": self.config.mode,
                "queries": queries,
                "video_id": video.id,
                "dataset": video.dataset,
                "source_chunks": [chunk.model_dump(mode="json") for chunk in video.source_chunks],
                "reported_caption_model": video.caption_model,
                "retrieved": [item.model_dump(mode="json") for item in retrieved.values()],
                "selected": [item.model_dump(mode="json") for item in result.moments],
            },
        )
        progress("Ready — compilation and source evidence saved")
        return result

    def get_result(self, edit_id: str) -> HighlightResult:
        path = self.config.output_dir / _safe_id(edit_id) / "result.json"
        if not path.exists():
            raise ServiceError("Edit not found. Generate a compilation first.")
        result = HighlightResult.model_validate(read_json(path))
        if result.mode != self.config.mode:
            raise ServiceError("This edit belongs to a different mode.")
        return result

    def revise_edit(self, edit_id: str, instruction: str, progress=None) -> HighlightResult:
        previous = self.get_result(edit_id)
        video = self.get_video(previous.edit_plan.video_id)
        self._verify_source(video)
        if previous.edit_plan.source_path.resolve() != video.local_path.resolve():
            raise ServiceError("The edit no longer references the registered source.")
        plan = revise_plan(previous.edit_plan, instruction)
        (progress or (lambda _: None))("Rendering revised edit — source windows preserved")
        started = time.perf_counter()
        self.tracer.initialize()
        with self.tracer.span(
            "highlight.revise", {"previous_edit_id": edit_id, "instruction": instruction}
        ) as span:
            path = self._renderer(plan, self.config.output_dir / plan.id)
            span["output"] = {"edit_id": plan.id, "duration": plan.duration}
        ids = {item for window in plan.windows for item in window.moment_ids}
        result = HighlightResult(
            edit_plan=plan,
            output_path=path,
            actual_duration=probe_media(path).duration,
            moments=[item for item in previous.moments if item.id in ids],
            timings={"render": round(time.perf_counter() - started, 3)},
            trace_url=span["url"],
            warnings=list(previous.warnings),
            mode=self.config.mode,
        )
        if self.tracer.warning and self.tracer.warning not in result.warnings:
            result.warnings.append(self.tracer.warning)
        write_json(self.config.output_dir / plan.id / "result.json", result)
        write_json(self.config.output_dir / plan.id / "edit_plan.json", plan)
        return result

    def undo_edit(self, edit_id: str) -> HighlightResult:
        previous_id = self.get_result(edit_id).edit_plan.previous_edit_id
        if not previous_id:
            raise ServiceError("This is the original edit; there is no earlier revision.")
        return self.get_result(previous_id)

    def preview_moment(self, edit_id: str, index: int) -> Path:
        plan = self.get_result(edit_id).edit_plan
        video = self.get_video(plan.video_id)
        self._verify_source(video)
        if plan.source_path.resolve() != video.local_path.resolve():
            raise ServiceError("The edit no longer references the registered source.")
        if not 0 <= index < len(plan.windows):
            raise ServiceError("Choose a clip from the current edit.")
        window = plan.windows[index]
        return preview_window(
            plan.source_path,
            window.start_seconds,
            window.end_seconds,
            self.config.output_dir / plan.id / f"preview_{index}",
        )

    def register_sample(self) -> VideoRecord:
        if self.config.mode != "offline_sample":
            raise ServiceError(
                "Set HIGHLIGHT_MODE=offline_sample explicitly to use synthetic fixtures."
            )
        existing = next(
            (
                video
                for video in self.list_videos()
                if video.label == "Synthetic pipeline sample · not gameplay"
            ),
            None,
        )
        if existing and existing.local_path.exists():
            return existing
        sample_path = self.config.data_dir / "synthetic-panels.mp4"
        colors = ["0x4ade80", "0xfb923c", "0x64748b", "0x4ade80", "0xfb923c", "0x64748b"]
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for color in colors:
            args += ["-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=15:d=10"]
        args += [
            "-filter_complex",
            "".join(f"[{i}:v]" for i in range(6)) + "concat=n=6:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(sample_path),
        ]
        try:
            subprocess.run(args, check=True, capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError):
            raise ServiceError(
                "Could not generate the local synthetic sample. Check FFmpeg."
            ) from None
        video = self.register_video(sample_path, "Synthetic pipeline sample · not gameplay")
        video = self._save_video(video.model_copy(update={"index_status": "ready"}))
        categories = ["clean_landing", "crash", "ordinary"] * 2
        rows = []
        for index, category in enumerate(categories):
            moment = Moment(
                id=f"sample_{index}",
                video_id=video.id,
                start_seconds=index * 10 + 2,
                end_seconds=index * 10 + 7,
                score=9 - index * 0.2,
                caption=f"Synthetic {category} fixture: {colors[index]} color panel, not actual gameplay.",
                reason="Manually labeled test fixture; no model inference.",
                evidence_type="manual",
                category=category,
            )
            rows.append(moment.model_dump(mode="json"))
        write_json(self._index_path(video.id), rows)
        return video

    def _sample_selection(self, video: VideoRecord, prompt: str) -> list[Moment]:
        lowered = prompt.lower()
        if any(word in lowered for word in ("crash", "roast", "fail", "rollover")):
            category = "crash"
        elif any(word in lowered for word in ("pro", "clean", "land", "success")):
            category = "clean_landing"
        else:
            raise SelectionError(
                "The synthetic sample supports only the two displayed gameplay briefs."
            )
        return [
            Moment.model_validate(row)
            for row in self.indexed_segments(video.id)
            if row.get("category") == category
        ]
