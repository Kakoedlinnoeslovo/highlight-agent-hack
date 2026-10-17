"""Small adapter for the hosted VSS Blueprint API, with strict source boundaries.

The public contract is documented in the upstream video-backend project. This
adapter deliberately rejects unknown response shapes instead of inventing times.
"""

from __future__ import annotations

import hashlib
import math
import mimetypes
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .models import Moment, VideoRecord


class ProviderError(RuntimeError):
    """An actionable provider failure whose message contains no credentials."""


GAMEPLAY_CAPTION_PROMPT = (
    "Describe only visible vehicle actions in this gameplay segment. Include the setup, "
    "vehicle movement, jumps, collisions, rolls, orientation on landing, and the visible "
    "aftermath. Distinguish a clean upright landing followed by continued driving from "
    "a crash or failed landing. Describe ordinary driving accurately when no stunt or "
    "collision occurs. State uncertainty when the outcome is not visible. Ignore HUD "
    "text, score displays, menus, and subtitles. Do not judge entertainment value or "
    "invent actions, intent, or events outside this segment."
)


def _s3_uri(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("s3://"):
        raise ProviderError(f"VAST {field} must be a canonical s3://bucket/key URI.")
    parsed = urlsplit(value)
    if (
        not parsed.netloc
        or not parsed.path.strip("/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ProviderError(f"VAST {field} must be a canonical s3://bucket/key URI.")
    return value


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProviderError(
            f"VAST segment is missing a valid {field}; check the deployment schema."
        )
    try:
        result = float(value)
    except ValueError as exc:
        raise ProviderError(
            f"VAST segment has an invalid {field}; check the deployment schema."
        ) from exc
    if not math.isfinite(result):
        raise ProviderError(f"VAST segment has a non-finite {field}.")
    return result


class VASTClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        base = settings.vast_base_url.rstrip("/")
        if base:
            parsed = urlsplit(base)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ProviderError(
                    "VAST_BASE_URL must be an HTTP(S) host without embedded credentials or query parameters."
                )
        self.api_base = base if base.endswith("/api/v1") else base + "/api/v1"
        timeout = settings.request_timeout
        if not math.isfinite(timeout) or timeout <= 0:
            raise ProviderError("HIGHLIGHT_REQUEST_TIMEOUT must be a positive number.")
        self.timeout = httpx.Timeout(timeout, connect=min(10, timeout))
        self.client = client or httpx.Client(timeout=self.timeout, follow_redirects=False)
        self._owns_client = client is None
        self._token = settings.vast_access_token.strip()
        self._bucket = settings.vast_upload_bucket.strip()
        self.raw_models: set[str] = set()
        self._models_by_source: dict[str, set[str]] = {}
        self.deployment_model: str | None = None
        self.dataset_inventory: list[dict] = []

    def models_for_source(self, original_video: str) -> set[str]:
        """Return observed caption model IDs for this exact source, as a copy."""
        return set(self._models_by_source.get(original_video, ()))

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        if not self.settings.vast_base_url:
            raise ProviderError("Set VAST_BASE_URL to the assigned team backend.")
        headers = dict(kwargs.pop("headers", {}))
        if self.settings.vast_host_header:
            # SSH forwards connect locally; the ingress still routes by the original host.
            headers["Host"] = self.settings.vast_host_header
        try:
            return self.client.request(method, url, timeout=self.timeout, headers=headers, **kwargs)
        except httpx.TimeoutException:
            raise ProviderError("VAST request timed out. Check the team VM and retry.") from None
        except httpx.HTTPError:
            raise ProviderError(
                "Cannot reach VAST. Check VAST_BASE_URL and the team VM connection."
            ) from None

    @staticmethod
    def _json(response: httpx.Response, *, context: str) -> dict:
        try:
            payload = response.json()
        except ValueError:
            raise ProviderError(
                f"VAST {context} did not return JSON; inspect the assigned deployment schema."
            ) from None
        if not isinstance(payload, dict):
            raise ProviderError(
                f"VAST {context} returned an unsupported shape; inspect the assigned deployment schema."
            )
        return payload

    @staticmethod
    def _check_status(response: httpx.Response, method: str, path: str) -> None:
        if response.is_success:
            return
        if response.status_code in {401, 403}:
            raise ProviderError(
                "VAST authentication failed. Check the access token or team username and password."
            )
        # Do not include raw server errors, request URLs, headers, or credentials.
        raise ProviderError(
            f"VAST {method} {path} returned HTTP {response.status_code}. "
            "Check the assigned deployment's API schema and service status."
        )

    def _login(self) -> None:
        if not self.settings.vast_username or not self.settings.vast_password:
            raise ProviderError("Set VAST_ACCESS_TOKEN or both VAST_USERNAME and VAST_PASSWORD.")
        response = self._send(
            "POST",
            self.api_base + "/auth/login",
            json={
                "username": self.settings.vast_username,
                "password": self.settings.vast_password,
            },
        )
        self._check_status(response, "POST", "/auth/login")
        token = self._json(response, context="login").get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise ProviderError(
                "VAST login returned no access_token; inspect the deployment schema."
            )
        self._token = token.strip()

    def _request(
        self, method: str, path: str, *, allow_status: tuple[int, ...] = (), **kwargs
    ) -> httpx.Response:
        if not self._token:
            self._login()
        headers = {"Authorization": f"Bearer {self._token}"}
        response = self._send(method, self.api_base + path, headers=headers, **kwargs)
        if (
            response.status_code == 401
            and self.settings.vast_username
            and self.settings.vast_password
        ):
            self._login()
            # Multipart streams are consumed by the first attempt, even when a
            # server rejects the request before accepting its body.
            for entry in kwargs.get("files", {}).values():
                stream = entry[1] if isinstance(entry, tuple) else entry
                if hasattr(stream, "seek"):
                    stream.seek(0)
            headers["Authorization"] = f"Bearer {self._token}"
            response = self._send(method, self.api_base + path, headers=headers, **kwargs)
        if response.status_code not in allow_status:
            self._check_status(response, method, path)
        return response

    def _config(self) -> dict:
        payload = self._json(self._request("GET", "/config"), context="configuration")
        model = (
            payload.get("synthesis", {}).get("cosmos_model")
            if isinstance(payload.get("synthesis"), dict)
            else None
        )
        if isinstance(model, str) and model.strip():
            self.deployment_model = self._safe_model(model)
        return payload

    def _safe_model(self, value: str) -> str:
        # Only model identifiers are retained; configuration and credentials are
        # never copied into output manifests or doctor diagnostics.
        value = value.strip()[:256]
        for secret in (self.settings.vast_password, self._token, self.settings.wandb_api_key):
            if secret:
                value = value.replace(secret, "[redacted]")
        return value

    def _upload_bucket(self) -> str:
        if not self._bucket:
            config = self._config()
            s3 = config.get("s3")
            bucket = s3.get("s3_upload_bucket") if isinstance(s3, dict) else None
            if isinstance(bucket, str):
                self._bucket = bucket.strip()
        if not self._bucket or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", self._bucket):
            raise ProviderError(
                "VAST upload bucket is unavailable. Set VAST_UPLOAD_BUCKET or confirm /config exposes s3.s3_upload_bucket before uploading."
            )
        return self._bucket

    def upload(self, video: VideoRecord, *, caption_prompt: str = GAMEPLAY_CAPTION_PROMPT) -> str:
        if not isinstance(caption_prompt, str) or not caption_prompt.strip() or len(caption_prompt) > 800:
            raise ProviderError("A caption prompt must contain between 1 and 800 characters.")
        # Determine the bucket before creating a remote upload: an object key
        # alone is insufficient for exact original_video filters afterward.
        bucket = self._upload_bucket()
        source = Path(video.local_path).expanduser().resolve()
        if not source.is_file():
            raise ProviderError(
                "The local video file is missing; register a readable recording before uploading."
            )
        try:
            with source.open("rb") as handle:
                response = self._request(
                    "POST",
                    "/videos/upload",
                    files={
                        "file": (
                            source.name,
                            handle,
                            mimetypes.guess_type(source.name)[0] or "video/mp4",
                        )
                    },
                    data={
                        "custom_prompt": caption_prompt,
                        "is_public": "false",
                        "tags": "highlight-agent,gameplay",
                    },
                )
        except OSError:
            raise ProviderError("The local recording could not be opened for upload.") from None
        payload = self._json(response, context="upload")
        if payload.get("success") is not True:
            raise ProviderError(
                "VAST did not confirm upload success; inspect the assigned deployment schema."
            )
        key = payload.get("object_key")
        if not isinstance(key, str) or not key.strip():
            raise ProviderError(
                "VAST upload returned no object_key; inspect the assigned deployment schema."
            )
        if key.startswith("s3://"):
            return _s3_uri(key, field="object_key")
        if key.startswith("/") or "://" in key or "?" in key or "#" in key:
            raise ProviderError(
                "VAST upload returned an ambiguous object_key; expected a relative S3 key."
            )
        return _s3_uri(f"s3://{bucket}/{key}", field="original_video")

    def dataset_sources(self) -> list[dict]:
        """Browse every supplied public recording, retaining independent contiguous runs."""
        streams: dict[str, list[dict]] = {}
        seen: set[str] = set()
        raw_chunks = []
        offset = 0
        while True:
            payload = self._json(
                self._request("GET", "/videos/explore", params={
                    "scope": "public", "indexed": "complete",
                    "limit": 100, "offset": offset,
                }), context="dataset listing",
            )
            rows = payload.get("chunks")
            total = payload.get("total")
            if not isinstance(rows, list) or type(total) is not int or total < 0:
                raise ProviderError("VAST dataset listing has an unsupported response shape.")
            if payload.get("table_available") is False:
                raise ProviderError("The supplied dataset index is unavailable. Retry when VAST is ready.")
            for row in rows:
                if not isinstance(row, dict):
                    raise ProviderError("VAST dataset listing contains a malformed row.")
                if row.get("is_public") is not True:
                    continue
                original = _s3_uri(row.get("original_video"), field="dataset source")
                if original in seen:
                    continue
                seen.add(original)
                raw_chunks.append(row)
                duration = _finite_number(row.get("chunk_duration_sec"), "chunk_duration_sec")
                index = row.get("chunk_index")
                count = row.get("total_segments")
                stream = row.get("stream_id")
                if (duration <= 0 or type(index) is not int or index < 0
                    or type(count) is not int or count <= 0 or not isinstance(stream, str) or not stream):
                    raise ProviderError("The public dataset is missing measured duration or stream ordering metadata.")
                metadata = {}
                for field in ("camera_id", "location", "capture_type"):
                    value = row.get(field)
                    if not isinstance(value, str) or not value.strip():
                        raise ProviderError(f"The public dataset is missing {field} metadata.")
                    metadata[field] = value
                streams.setdefault(stream, []).append({
                    "original_video": original, "duration": duration, "chunk_index": index,
                    "segment_count": count, **metadata,
                })
            offset += len(rows)
            if offset >= total:
                break
            if not rows or offset > 10000:
                raise ProviderError("VAST dataset pagination did not complete. Retry dataset browsing.")
        sources = []
        for stream, chunks in streams.items():
            chunks.sort(key=lambda item: item["chunk_index"])
            runs = [[chunks[0]]]
            for chunk in chunks[1:]:
                if chunk["chunk_index"] == runs[-1][-1]["chunk_index"]:
                    raise ProviderError("The public dataset has conflicting objects for a stream chunk.")
                if chunk["chunk_index"] != runs[-1][-1]["chunk_index"] + 1:
                    runs.append([])
                runs[-1].append(chunk)
            for contiguous in runs:
                duration = sum(chunk["duration"] for chunk in contiguous)
                first = contiguous[0]
                metadata = {key: first[key] for key in ("camera_id", "location", "capture_type")}
                if any(any(chunk[key] != value for key, value in metadata.items())
                       for chunk in contiguous):
                    raise ProviderError("The public dataset has inconsistent recording metadata.")
                location = metadata["location"].replace("_", " ").title()
                camera = metadata["camera_id"]
                sources.append({
                    "original_video": first["original_video"], "stream_id": stream, **metadata,
                    "label": f"{location} · {camera} · {stream[:8]} · {duration:.0f}s",
                    "duration": duration,
                    "segment_count": sum(c["segment_count"] for c in contiguous),
                    "chunks": contiguous,
                })
        self.dataset_inventory = raw_chunks
        return sorted(sources, key=lambda item: (item["label"], item["original_video"]))

    def download_dataset_source(self, original: str, destination: Path) -> Path:
        """Stream an exact S3 object via the authenticated backend, without retaining its URL."""
        original = _s3_uri(original, field="dataset source")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not self._token:
            self._login()
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self._token}"}
            if self.settings.vast_host_header:
                headers["Host"] = self.settings.vast_host_header
            try:
                with self.client.stream(
                    "GET", self.api_base + "/videos/stream", headers=headers,
                    params={"source": original, "token": self._token}, timeout=self.timeout,
                ) as response:
                    if (response.status_code == 401 and attempt == 0
                        and self.settings.vast_username and self.settings.vast_password):
                        self._login()
                        continue
                    self._check_status(response, "GET", "/videos/stream")
                    if response.status_code != 200:
                        raise ProviderError("VAST did not return a complete dataset video.")
                    with tempfile.TemporaryDirectory(prefix=".download-", dir=destination.parent) as folder:
                        temporary = Path(folder) / "source.mp4"
                        size = 0
                        with temporary.open("wb") as handle:
                            for block in response.iter_bytes(1024 * 1024):
                                size += len(block)
                                if size > 512 * 1024 * 1024:
                                    raise ProviderError("Dataset chunk exceeds the 512 MB download limit.")
                                handle.write(block)
                        expected = response.headers.get("content-length")
                        if not size or (expected is not None and expected != str(size)):
                            raise ProviderError("Dataset download was incomplete. Retry the import.")
                        temporary.replace(destination)
                    return destination
            except httpx.HTTPError:
                raise ProviderError("Dataset download failed. Check the VAST connection and retry.") from None
        raise ProviderError("Dataset authentication failed. Check the VAST credentials.")

    @staticmethod
    def _original(video: VideoRecord) -> str:
        if not video.original_video:
            raise ProviderError(
                "This recording has no VAST source identifier; upload or attach its exact original_video URI first."
            )
        return _s3_uri(video.original_video, field="original_video")

    def _normalize(self, row: dict, video: VideoRecord) -> Moment:
        original = row.get("original_video") if video.source_chunks else self._original(video)
        chunk = next((c for c in video.source_chunks if c.original_video == original), None)
        if video.source_chunks and chunk is None:
            raise ProviderError("VAST returned a source outside the registered dataset excerpt.")
        offset = chunk.start_seconds if chunk else 0
        duration = chunk.duration if chunk else video.duration
        start = _finite_number(row.get("segment_start_sec"), "segment_start_sec")
        end_value = row.get("segment_end_sec")
        if end_value is None:
            length = _finite_number(row.get("duration"), "measured segment duration")
            if length <= 0:
                raise ProviderError(
                    "VAST segment has no valid measured duration; an end timestamp cannot be inferred."
                )
            end = start + length
        else:
            end = _finite_number(end_value, "segment_end_sec")
        if start < 0 or end <= start or end > duration + (0.05 if chunk else 0.00001):
            raise ProviderError(
                "VAST returned source timestamps outside the matching local recording. Confirm the exact source file and deployment schema."
            )
        caption = row.get("reasoning_content")
        if not isinstance(caption, str) or not caption.strip():
            raise ProviderError(
                "VAST segment is missing its reasoning_content caption; ingestion may still be incomplete."
            )
        similarity = _finite_number(row.get("similarity_score", 0), "similarity_score")
        if not -1 <= similarity <= 1:
            raise ProviderError(
                "VAST similarity_score is outside the documented cosine score range."
            )
        source = row.get("source")
        if source is not None:
            source = _s3_uri(source, field="segment source")
        identity = f"{original}|{source or row.get('segment_number', '')}|{start:.9f}|{end:.9f}"
        model = row.get("cosmos_model")
        if isinstance(model, str) and model.strip():
            model = self._safe_model(model)
            self.raw_models.add(model)
            self._models_by_source.setdefault(original, set()).add(model)
        return Moment(
            id="moment-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            video_id=video.id,
            start_seconds=start + offset,
            end_seconds=min(end, duration) + offset,
            caption=caption.strip(),
            score=max(0, similarity) * 10,
            reason="Retrieved from indexed visual captions; not freshly verified against video.",
            source=source,
            evidence_type="caption_ranked",
        )

    def _segments_for_source(self, video: VideoRecord, original: str) -> list[dict]:
        payload = self._json(
            self._request(
                "GET",
                "/tools/segments",
                params={
                    "original_video": original,
                },
            ),
            context="segment listing",
        )
        if payload.get("original_video") != original or not isinstance(
            payload.get("segments"), list
        ):
            raise ProviderError(
                "VAST segment listing does not identify the exact requested source; inspect the deployment schema."
            )
        normalized = []
        seen = set()
        for row in payload["segments"]:
            if not isinstance(row, dict):
                raise ProviderError("VAST segment listing contains a malformed row.")
            if row.get("original_video") != original:
                continue
            moment = self._normalize(row, video)
            if moment.id in seen:
                continue
            seen.add(moment.id)
            result = moment.model_dump(mode="json")
            result["original_video"] = original
            model = row.get("cosmos_model")
            if isinstance(model, str) and model.strip():
                result["cosmos_model"] = self._safe_model(model)
            for field in ("segment_number", "total_segments"):
                value = row.get(field)
                if value is not None:
                    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                        raise ProviderError(f"VAST {field} must be a positive integer.")
                    result[field] = value
            normalized.append(result)
        return sorted(normalized, key=lambda row: row["start_seconds"])

    def segments(self, video: VideoRecord) -> list[dict]:
        if not video.source_chunks:
            return self._segments_for_source(video, self._original(video))
        rows = []
        slot_offset = 0
        if not self._token:
            self._login()
        with ThreadPoolExecutor(max_workers=3) as executor:
            batches = list(executor.map(
                lambda chunk: self._segments_for_source(video, chunk.original_video), video.source_chunks,
            ))
        for source_rows in batches:
            totals = {row.get("total_segments") for row in source_rows}
            if not source_rows or len(totals) != 1 or None in totals:
                raise ProviderError("A dataset chunk has no complete segment count. Retry import later.")
            expected = totals.pop()
            numbers = [row.get("segment_number") for row in source_rows]
            if None in numbers:
                raise ProviderError("A dataset chunk is missing its source segment number.")
            if len(numbers) != len(set(numbers)) or any(number > expected for number in numbers):
                raise ProviderError("A dataset chunk has duplicate or out-of-range segment numbers.")
            for row in source_rows:
                row["source_segment_number"] = row["segment_number"]
                row["source_total_segments"] = row.pop("total_segments")
                row["segment_number"] += slot_offset
            rows.extend(source_rows)
            slot_offset += expected
        for row in rows:
            row["total_segments"] = slot_offset
        return rows

    def search(self, video: VideoRecord, query: str, top_k: int = 16) -> list[Moment]:
        if video.source_chunks:
            # The deployment treats list metadata filters as scalar strings. Search
            # each exact object, then merge before the single application ranking.
            if not self._token:
                self._login()
            with ThreadPoolExecutor(max_workers=3) as executor:
                batches = list(executor.map(
                    lambda chunk: self._search_source(video, query, top_k, chunk.original_video),
                    video.source_chunks,
                ))
            return sorted((item for batch in batches for item in batch),
                          key=lambda item: (-item.score, item.start_seconds))[:top_k]
        return self._search_source(video, query, top_k, self._original(video))

    def _search_source(self, video: VideoRecord, query: str, top_k: int, original: str) -> list[Moment]:
        if not isinstance(query, str) or not query.strip():
            raise ProviderError("Provide a nonempty visual search query.")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 32:
            raise ProviderError("VAST top_k must be between 1 and 32.")
        body = {
            "query": query.strip(),
            "top_k": top_k,
            "metadata_filters": {"original_video": original},
            "llm_top_n": 1,
            "include_public": video.dataset in {"sf-streets", "hackathon-public"},
            "min_similarity": 0.1,
        }
        response = self._request("POST", "/tools/search", json=body, allow_status=(404, 405))
        if response.status_code in {404, 405}:
            response = self._request("POST", "/search", json=body)
        payload = self._json(response, context="search")
        if not isinstance(payload.get("results"), list):
            raise ProviderError(
                "VAST search returned no results list; inspect the assigned deployment schema."
            )
        moments = []
        seen = set()
        for row in payload["results"]:
            if not isinstance(row, dict):
                raise ProviderError("VAST search returned a malformed result row.")
            if row.get("original_video") != original:
                continue
            moment = self._normalize(row, video)
            if moment.id not in seen:
                moments.append(moment)
                seen.add(moment.id)
            if len(moments) >= top_k:
                break
        return moments

    def doctor(self) -> dict:
        """Inspect capabilities and configured model IDs without exposing config."""
        report: dict[str, Any] = {
            "provider": "VAST",
            "schema_available": False,
            "config_available": False,
            "supported_endpoints": [],
            "configured_synthesis_model": self.deployment_model,
            "observed_caption_models": sorted(self.raw_models),
            "model_evidence_note": "Configured synthesis models are not proof of caption execution; observed models come from returned caption rows.",
            "upload_bucket_configured": bool(self._bucket),
            "errors": [],
        }
        if not self.settings.vast_base_url:
            report["errors"].append("Set VAST_BASE_URL to inspect the assigned team deployment.")
            return report
        schema_url = self.api_base.removesuffix("/api/v1") + "/openapi.json"
        try:
            response = self._send("GET", schema_url)
            self._check_status(response, "GET", "/openapi.json")
            schema = self._json(response, context="OpenAPI schema")
            paths = schema.get("paths")
            if not isinstance(paths, dict):
                raise ProviderError("VAST OpenAPI schema has no paths object.")
            report["schema_available"] = True
            endpoints = (
                "/auth/login",
                "/videos/upload",
                "/tools/segments",
                "/tools/search",
                "/search",
                "/config",
            )
            report["supported_endpoints"] = [
                path for path in endpoints if "/api/v1" + path in paths or path in paths
            ]
        except ProviderError as exc:
            report["errors"].append(str(exc))
        try:
            config = self._config()
            report["config_available"] = True
            report["configured_synthesis_model"] = self.deployment_model
            s3 = config.get("s3")
            report["upload_bucket_configured"] = bool(
                self._bucket or (isinstance(s3, dict) and s3.get("s3_upload_bucket"))
            )
        except ProviderError as exc:
            report["errors"].append(str(exc))
        return report
