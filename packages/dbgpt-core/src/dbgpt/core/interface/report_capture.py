"""Agent-loop-neutral capture context for report execution artifacts."""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterator, List, Optional, Protocol, runtime_checkable

from pydantic import TypeAdapter

from .report import (
    MAX_ARTIFACT_ID_LENGTH,
    MAX_CONV_UID_LENGTH,
    MAX_MESSAGE_ID_LENGTH,
    MAX_RUN_ID_LENGTH,
    MAX_STEP_ID_LENGTH,
    MAX_TASK_ID_LENGTH,
    Artifact,
    ExecutionReference,
)

_ARTIFACT_ADAPTER = TypeAdapter(Artifact)


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


def sha256_bytes(value: bytes) -> str:
    """Hash exact bytes using lowercase SHA-256 hex encoding."""
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    """Hash UTF-8 text without platform-dependent newline conversion."""
    return sha256_bytes(value.encode("utf-8"))


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
    "InMemoryArtifactSink",
    "bind_artifact_sink",
    "get_current_artifact_sink",
    "sha256_bytes",
    "sha256_json",
    "sha256_text",
]
