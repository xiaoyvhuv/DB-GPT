"""Agent-loop-neutral capture context for report execution artifacts."""

from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import logging
import math
import re
import threading
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Literal,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    cast,
    runtime_checkable,
)
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import TypeAdapter

from .report import (
    MAX_ARTIFACT_ID_LENGTH,
    MAX_CONV_UID_LENGTH,
    MAX_MESSAGE_ID_LENGTH,
    MAX_RUN_ID_LENGTH,
    MAX_STEP_ID_LENGTH,
    MAX_TASK_ID_LENGTH,
    Artifact,
    ArtifactTiming,
    ExecutionReference,
    ReferenceArtifact,
)

_ARTIFACT_ADAPTER = TypeAdapter(Artifact)
MAX_REFERENCE_EXCERPT_CHARS = 4_000
logger = logging.getLogger(__name__)
_SENSITIVE_QUERY_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "code",
    "credential",
    "key",
    "password",
    "passwd",
    "secret",
    "session",
    "signature",
    "sig",
    "token",
    "x-amz-credential",
    "x-amz-signature",
    "x-amz-security-token",
    "x-goog-credential",
    "x-goog-signature",
}

_SENSITIVE_QUERY_PARTS = {
    "credential",
    "key",
    "password",
    "passwd",
    "secret",
    "signature",
    "token",
}
_SENSITIVE_COMPACT_QUERY_NAMES = {
    "apikey",
    "accesskey",
    "accesskeyid",
    "awsaccesskeyid",
    "clientsecret",
    "idtoken",
    "privatekey",
    "refreshtoken",
    "secretkey",
}


def _uuid_token() -> str:
    return uuid.uuid4().hex


def _require_text(name: str, value: str, max_length: Optional[int] = None) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    if max_length is not None and len(value) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return value


def _validate_optional_text(name: str, value: Optional[str], max_length: int) -> None:
    if value is not None:
        _require_text(name, value, max_length)


def _validate_artifact_persistence_coordinates(artifact: Artifact) -> None:
    _require_text("artifact_id", artifact.artifact_id, MAX_ARTIFACT_ID_LENGTH)
    for input_id in artifact.input_artifact_ids:
        _require_text("input_artifact_id", input_id, MAX_ARTIFACT_ID_LENGTH)

    execution = artifact.execution
    _require_text("execution.conv_uid", execution.conv_uid, MAX_CONV_UID_LENGTH)
    _require_text("execution.step_id", execution.step_id, MAX_STEP_ID_LENGTH)
    _validate_optional_text(
        "execution.message_id", execution.message_id, MAX_MESSAGE_ID_LENGTH
    )
    _validate_optional_text("execution.task_id", execution.task_id, MAX_TASK_ID_LENGTH)
    _validate_optional_text("execution.run_id", execution.run_id, MAX_RUN_ID_LENGTH)


@dataclass(frozen=True)
class ArtifactCaptureContext:
    """Correlation metadata and identifier generation for one capture run."""

    conv_uid: str
    run_id: Optional[str] = None
    message_id: Optional[str] = None
    task_id: Optional[str] = None
    id_factory: Callable[[], str] = field(
        default=_uuid_token,
        repr=False,
        compare=False,
    )
    _id_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _require_text("conv_uid", self.conv_uid, MAX_CONV_UID_LENGTH)
        _validate_optional_text("run_id", self.run_id, MAX_RUN_ID_LENGTH)
        _validate_optional_text("message_id", self.message_id, MAX_MESSAGE_ID_LENGTH)
        _validate_optional_text("task_id", self.task_id, MAX_TASK_ID_LENGTH)

    def _next_token(self) -> str:
        with self._id_lock:
            token = str(self.id_factory())
        return _require_text("generated id", token)

    def new_artifact_id(self, artifact_type: str) -> str:
        """Return an opaque ID with a human-readable artifact type prefix."""
        prefix = _require_text("artifact_type", artifact_type).strip()
        return _require_text(
            "artifact_id",
            f"{prefix}-{self._next_token()}",
            MAX_ARTIFACT_ID_LENGTH,
        )

    def execution_reference(
        self,
        *,
        action: str,
        step_id: Optional[str] = None,
    ) -> ExecutionReference:
        """Create a reference for one real action invocation."""
        action = _require_text("action", action)
        resolved_step_id = _require_text(
            "step_id",
            step_id or f"{action}-{self._next_token()}",
            MAX_STEP_ID_LENGTH,
        )
        return ExecutionReference(
            conv_uid=self.conv_uid,
            step_id=resolved_step_id,
            message_id=self.message_id,
            task_id=self.task_id,
            run_id=self.run_id,
            action=action,
        )


@runtime_checkable
class ArtifactSink(Protocol):
    """Minimal producer contract for recording typed execution artifacts."""

    @property
    def context(self) -> ArtifactCaptureContext:
        """Return correlation metadata for this sink."""

    def record(self, artifact: Artifact) -> None:
        """Record one artifact owned by this capture context."""

    def snapshot(self) -> List[Artifact]:
        """Return an immutable-by-ownership snapshot of recorded artifacts."""


class InMemoryArtifactSink:
    """Thread-safe run-level artifact collector used before report assembly."""

    def __init__(self, context: ArtifactCaptureContext):
        self._context = context
        self._artifacts: List[Artifact] = []
        self._artifact_ids: set[str] = set()
        self._lock = threading.Lock()

    @property
    def context(self) -> ArtifactCaptureContext:
        """Return correlation metadata for this sink."""
        return self._context

    @property
    def artifact_count(self) -> int:
        """Return the current count without copying artifact payloads."""
        with self._lock:
            return len(self._artifacts)

    def record(self, artifact: Artifact) -> None:
        """Record a defensive copy after ownership and uniqueness checks."""
        payload = artifact.model_dump(mode="python", warnings="none")
        created_at = payload.pop("created_at")
        _validate_json_value(payload, set())
        payload["created_at"] = (
            created_at.isoformat() if isinstance(created_at, datetime) else created_at
        )
        validated = _ARTIFACT_ADAPTER.validate_json(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )

        _validate_artifact_persistence_coordinates(validated)
        if validated.status is None or validated.duration_ms is None:
            raise ValueError("captured artifact requires status and duration")
        if validated.execution.conv_uid != self.context.conv_uid:
            raise ValueError(
                f"artifact {validated.artifact_id} belongs to conversation "
                f"{validated.execution.conv_uid}, expected {self.context.conv_uid}"
            )
        if validated.execution.run_id != self.context.run_id:
            raise ValueError(
                f"artifact {validated.artifact_id} belongs to run "
                f"{validated.execution.run_id}, expected {self.context.run_id}"
            )
        with self._lock:
            if validated.artifact_id in validated.input_artifact_ids:
                raise ValueError(
                    f"artifact {validated.artifact_id} cannot depend on itself"
                )
            if validated.artifact_id in self._artifact_ids:
                raise ValueError(f"duplicate artifact id: {validated.artifact_id}")
            for input_id in validated.input_artifact_ids:
                if input_id not in self._artifact_ids:
                    raise ValueError(f"unknown input artifact {input_id}")
            self._artifacts.append(validated)
            self._artifact_ids.add(validated.artifact_id)

    def snapshot(self) -> List[Artifact]:
        """Return deep copies so consumers cannot mutate captured evidence."""
        with self._lock:
            return [artifact.model_copy(deep=True) for artifact in self._artifacts]


_current_artifact_sink: contextvars.ContextVar[Optional[ArtifactSink]] = (
    contextvars.ContextVar("dbgpt_report_artifact_sink", default=None)
)


@contextmanager
def bind_artifact_sink(sink: ArtifactSink) -> Iterator[ArtifactSink]:
    """Bind a sink to the current context and restore nested bindings safely."""
    token = _current_artifact_sink.set(sink)
    try:
        yield sink
    finally:
        _current_artifact_sink.reset(token)


def get_current_artifact_sink() -> Optional[ArtifactSink]:
    """Return the sink bound to the current execution context, if any."""
    return _current_artifact_sink.get()


def sanitize_reference_uri(value: str) -> str:
    """Remove credentials from persisted HTTP(S) reference URLs."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"}:
            return value
        hostname = parsed.hostname or ""
        host = f"[{hostname}]" if ":" in hostname else hostname
        netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
        query = urlencode(
            [
                (name, "[REDACTED]" if _is_sensitive_query_name(name) else item)
                for name, item in parse_qsl(parsed.query, keep_blank_values=True)
            ],
            doseq=True,
        )
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))
    except Exception:
        logger.warning("Unable to sanitize reference URI; omitting it", exc_info=True)
        return ""


def _is_sensitive_query_name(name: str) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    if normalized in _SENSITIVE_QUERY_NAMES:
        return True
    parts = {part for part in re.split(r"[^a-z0-9]+", normalized) if part}
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    return bool(parts & _SENSITIVE_QUERY_PARTS) or compact in (
        _SENSITIVE_COMPACT_QUERY_NAMES
    )


def capture_reference_artifact(
    *,
    reference_type: Literal["knowledge", "memory", "web", "model"],
    source_id: object,
    action: str,
    duration_ms: int,
    status: Literal["succeeded", "failed"] = "succeeded",
    error: Optional[str] = None,
    source_name: Optional[object] = None,
    uri: Optional[object] = None,
    locator: Optional[Dict[str, Any]] = None,
    excerpt: Optional[str] = None,
    excerpt_parts: Optional[Iterable[str]] = None,
    excerpt_separator: str = "",
    score: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Record a knowledge, memory, web, or model reference if capture is active."""
    sink = get_current_artifact_sink()
    if sink is None:
        return None
    try:
        if excerpt is not None and excerpt_parts is not None:
            raise ValueError("provide excerpt or excerpt_parts, not both")
        excerpt_digest = hashlib.sha256()
        preview_parts: List[str] = []
        preview_chars = 0
        excerpt_chars = 0
        if excerpt_parts is not None:
            for index, part in enumerate(excerpt_parts):
                if index:
                    _update_text_digest(excerpt_digest, excerpt_separator)
                    excerpt_chars += len(excerpt_separator)
                    remaining = MAX_REFERENCE_EXCERPT_CHARS - preview_chars
                    if remaining > 0:
                        preview_separator = excerpt_separator[:remaining]
                        preview_parts.append(preview_separator)
                        preview_chars += len(preview_separator)
                text_part = str(part)
                _update_text_digest(excerpt_digest, text_part)
                excerpt_chars += len(text_part)
                remaining = MAX_REFERENCE_EXCERPT_CHARS - preview_chars
                if remaining > 0:
                    preview_part = text_part[:remaining]
                    preview_parts.append(preview_part)
                    preview_chars += len(preview_part)
            preview = "".join(preview_parts)
        else:
            complete_excerpt = excerpt or ""
            _update_text_digest(excerpt_digest, complete_excerpt)
            excerpt_chars = len(complete_excerpt)
            preview = complete_excerpt[:MAX_REFERENCE_EXCERPT_CHARS]
        summary = {
            "excerpt_chars": excerpt_chars,
            "excerpt_sha256": excerpt_digest.hexdigest(),
            "excerpt_truncated": excerpt_chars > MAX_REFERENCE_EXCERPT_CHARS,
        }
        normalized_locator = cast(
            Dict[str, Any], normalize_artifact_value(locator or {})
        )
        for key, value in normalized_locator.items():
            if isinstance(value, str) and (key == "url" or key.endswith("_url")):
                normalized_locator[key] = sanitize_reference_uri(value)
        normalized_metadata = cast(
            Dict[str, Any], normalize_artifact_value(metadata or {})
        )
        normalized_metadata["content_summary"] = summary
        resolved_score = (
            float(score)
            if isinstance(score, (int, float))
            and math.isfinite(float(score))
            and score >= 0
            else None
        )
        resolved_source_name = (
            sanitize_reference_uri(str(source_name))
            if source_name is not None
            else None
        )
        resolved_uri = sanitize_reference_uri(str(uri)) if uri is not None else None
        artifact = ReferenceArtifact(
            artifact_id=sink.context.new_artifact_id("reference"),
            execution=sink.context.execution_reference(action=action),
            created_at=datetime.now(timezone.utc),
            status=status,
            duration_ms=max(0, duration_ms),
            timing=ArtifactTiming(server_execution_ms=max(0, duration_ms)),
            error=error[:MAX_REFERENCE_EXCERPT_CHARS] if error is not None else None,
            reference_type=reference_type,
            source_id=str(source_id) if source_id is not None else "",
            source_name=resolved_source_name,
            uri=resolved_uri or None,
            locator=normalized_locator,
            excerpt=preview or None,
            score=resolved_score,
            metadata=normalized_metadata,
        )
        sink.record(artifact)
        return artifact.artifact_id
    except Exception:
        logger.warning("Unable to capture reference report artifact", exc_info=True)
        return None


def sha256_bytes(value: bytes) -> str:
    """Hash exact bytes using lowercase SHA-256 hex encoding."""
    return hashlib.sha256(value).hexdigest()


def _update_text_digest(digest: Any, value: str) -> None:
    for offset in range(0, len(value), 64 * 1024):
        digest.update(value[offset : offset + 64 * 1024].encode("utf-8"))


def sha256_text(value: str) -> str:
    """Hash UTF-8 text without platform-dependent newline conversion."""
    digest = hashlib.sha256()
    _update_text_digest(digest, value)
    return digest.hexdigest()


def normalize_artifact_value(value: Any) -> Any:
    """Normalize common execution values without ambiguous ``default=str``."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    value_type = type(value)
    if value_type.__module__.startswith("pandas.") and value_type.__name__ in {
        "NAType",
        "NaTType",
    }:
        return None
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return {"$number": "NaN"}
        return {"$number": "Infinity" if value > 0 else "-Infinity"}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$bytes_base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, Enum):
        return normalize_artifact_value(value.value)
    # SQLAlchemy Result rows are neither tuples nor dictionaries, but expose
    # their stable column/value view through ``_mapping``.  Treating the Row
    # object itself as unsupported discarded otherwise successful SQL evidence.
    row_mapping = getattr(value, "_mapping", None)
    if isinstance(row_mapping, Mapping):
        if not all(isinstance(key, str) for key in row_mapping):
            raise ValueError("artifact mappings require string keys")
        return {
            key: normalize_artifact_value(item) for key, item in row_mapping.items()
        }
    if isinstance(value, (list, tuple)):
        return [normalize_artifact_value(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("artifact mappings require string keys")
        return {key: normalize_artifact_value(item) for key, item in value.items()}
    item_method = getattr(value, "item", None)
    if callable(item_method):
        item = item_method()
        if item is not value:
            return normalize_artifact_value(item)
    raise ValueError(f"unsupported artifact value: {type(value).__name__}")


def summarize_tabular_artifact(
    columns: Sequence[str],
    rows: Iterable[Any],
    *,
    max_preview_rows: int,
    max_preview_bytes: int,
) -> Tuple[Dict[str, Any], str, int, bool]:
    """Hash normalized rows incrementally and retain a bounded inline preview."""
    normalized_columns = [str(column) for column in columns]

    def canonical_bytes(item: Any) -> bytes:
        return json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    digest = hashlib.sha256()
    digest.update(b'{"columns":')
    digest.update(canonical_bytes(normalized_columns))
    digest.update(b',"rows":[')
    preview_rows: List[Any] = []
    preview_bytes = len(canonical_bytes({"columns": normalized_columns, "rows": []}))
    row_count = 0
    for row in rows:
        normalized_row = normalize_artifact_value(row)
        encoded_row = canonical_bytes(normalized_row)
        if row_count:
            digest.update(b",")
        digest.update(encoded_row)
        if (
            len(preview_rows) < max_preview_rows
            and preview_bytes + len(encoded_row) + 1 <= max_preview_bytes
        ):
            preview_rows.append(normalized_row)
            preview_bytes += len(encoded_row) + 1
        row_count += 1
    digest.update(b"]}")
    truncated = len(preview_rows) < row_count
    preview: Dict[str, Any] = {"columns": normalized_columns, "rows": preview_rows}
    if truncated:
        preview.update({"complete_row_count": row_count, "truncated": True})
    return preview, digest.hexdigest(), row_count, truncated


def _validate_json_value(value: object, active_containers: set) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("value must be strict JSON-compatible data")
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in active_containers:
            raise ValueError("value must be strict JSON-compatible data")
        active_containers.add(identity)
        try:
            if isinstance(value, list):
                for item in value:
                    _validate_json_value(item, active_containers)
            else:
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise ValueError("value must be strict JSON-compatible data")
                    _validate_json_value(item, active_containers)
        finally:
            active_containers.remove(identity)
        return
    raise ValueError("value must be strict JSON-compatible data")


def sha256_json(value: object) -> str:
    """Hash strict canonical JSON, rejecting lossy or non-finite values."""
    _validate_json_value(value, set())
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("value must be strict JSON-compatible data") from error
    return sha256_text(payload)


__all__ = [
    "ArtifactCaptureContext",
    "ArtifactSink",
    "MAX_REFERENCE_EXCERPT_CHARS",
    "InMemoryArtifactSink",
    "bind_artifact_sink",
    "capture_reference_artifact",
    "get_current_artifact_sink",
    "normalize_artifact_value",
    "sanitize_reference_uri",
    "sha256_bytes",
    "sha256_json",
    "sha256_text",
    "summarize_tabular_artifact",
]
