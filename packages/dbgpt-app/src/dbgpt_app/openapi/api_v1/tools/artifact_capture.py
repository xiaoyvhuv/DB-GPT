"""Semantic report-artifact adapters for agentic execution tools."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from dbgpt.core.interface.report import (
    ArtifactContent,
    ChartArtifact,
    PythonArtifact,
    SQLArtifact,
)
from dbgpt.core.interface.report_capture import (
    ArtifactSink,
    get_current_artifact_sink,
    sha256_json,
    sha256_text,
    summarize_tabular_artifact,
)

logger = logging.getLogger(__name__)
MAX_CAPTURE_STREAM_CHARS = 50_000
MAX_CAPTURE_ERROR_CHARS = 8_000
MAX_SQL_PREVIEW_BYTES = 256 * 1024
MAX_SQL_PREVIEW_ROWS = 100


def _content_size(value: Any) -> int:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return len(payload.encode("utf-8"))


def _bounded_error(error: Optional[str]) -> Optional[str]:
    return error[:MAX_CAPTURE_ERROR_CHARS] if error is not None else None


def _error_summary(error: Optional[str]) -> dict:
    if error is None:
        return {}
    return {
        "error_chars": len(error),
        "error_sha256": sha256_text(error),
        "error_truncated": len(error) > MAX_CAPTURE_ERROR_CHARS,
    }


def _sha256_file(path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def connector_database_name(
    database_connector: Any, artifact_sink: Optional[ArtifactSink] = None
) -> Optional[str]:
    """Return the current DB identity without exposing connector credentials."""
    if artifact_sink is None and get_current_artifact_sink() is None:
        return None
    try:
        name = getattr(database_connector, "_db_name", None)
        if name:
            return str(name)
        getter = getattr(database_connector, "get_current_db_name", None)
        if callable(getter):
            value = getter()
            return str(value) if value else None
    except Exception:
        logger.debug("Unable to resolve connector database name", exc_info=True)
    return None


def capture_sql_execution(
    *,
    sql: str,
    database: Optional[str],
    duration_ms: int,
    columns: Optional[Sequence[str]] = None,
    rows: Optional[Sequence[Any]] = None,
    error: Optional[str] = None,
    artifact_sink: Optional[ArtifactSink] = None,
) -> Optional[str]:
    """Record one real SQL attempt; capture failures never alter tool behavior."""
    sink = artifact_sink or get_current_artifact_sink()
    if sink is None:
        return None

    try:
        artifact_id = sink.context.new_artifact_id("sql")
        normalized_columns = [str(column) for column in (columns or [])]
        failed = error is not None
        result = None
        complete_sha256 = None
        row_count = None
        preview_truncated = False
        if not failed:
            result_value, complete_sha256, row_count, preview_truncated = (
                summarize_tabular_artifact(
                    normalized_columns,
                    rows or [],
                    max_preview_rows=MAX_SQL_PREVIEW_ROWS,
                    max_preview_bytes=MAX_SQL_PREVIEW_BYTES,
                )
            )
            result = ArtifactContent(
                inline=result_value,
                media_type="application/json",
                sha256=sha256_json(result_value),
                size=_content_size(result_value),
            )
        artifact = SQLArtifact(
            artifact_id=artifact_id,
            execution=sink.context.execution_reference(action="sql_query"),
            created_at=datetime.now(timezone.utc),
            status="failed" if failed else "succeeded",
            duration_ms=max(0, duration_ms),
            error=_bounded_error(error),
            sql=sql,
            database=database,
            display_type="response_table",
            columns=normalized_columns,
            row_count=row_count,
            result=result,
            metadata={
                "input_summary": {
                    "sql_chars": len(sql),
                    "sql_sha256": sha256_text(sql),
                },
                "output_summary": {
                    "column_count": len(normalized_columns),
                    "row_count": row_count,
                    "complete_result_sha256": complete_sha256,
                    "preview_truncated": preview_truncated,
                    **_error_summary(error),
                },
            },
        )
        sink.record(artifact)
        return artifact_id
    except Exception:
        logger.warning("Unable to capture SQL report artifact", exc_info=True)
        return None


def capture_python_execution(
    *,
    code: str,
    duration_ms: int,
    status: str,
    exit_code: Optional[int],
    stdout: str,
    stderr: str,
    error: Optional[str] = None,
    image_urls: Optional[Sequence[str]] = None,
    input_artifact_ids: Optional[Sequence[str]] = None,
    artifact_sink: Optional[ArtifactSink] = None,
) -> Optional[str]:
    """Record subprocess state before user-facing output is truncated."""
    sink = artifact_sink or get_current_artifact_sink()
    if sink is None:
        return None

    try:
        artifact_id = sink.context.new_artifact_id("python")
        urls = list(image_urls or [])
        result = ArtifactContent(inline={"images": urls}) if urls else None
        artifact = PythonArtifact(
            artifact_id=artifact_id,
            execution=sink.context.execution_reference(action="code_interpreter"),
            input_artifact_ids=list(input_artifact_ids or []),
            created_at=datetime.now(timezone.utc),
            status=status,
            duration_ms=max(0, duration_ms),
            error=_bounded_error(error),
            code=code,
            exit_code=exit_code,
            stdout=stdout[:MAX_CAPTURE_STREAM_CHARS],
            stderr=stderr[:MAX_CAPTURE_STREAM_CHARS],
            result=result,
            metadata={
                "input_summary": {
                    "code_chars": len(code),
                    "code_sha256": sha256_text(code),
                },
                "output_summary": {
                    "stdout_chars": len(stdout),
                    "stdout_sha256": sha256_text(stdout),
                    "stdout_truncated": len(stdout) > MAX_CAPTURE_STREAM_CHARS,
                    "stderr_chars": len(stderr),
                    "stderr_sha256": sha256_text(stderr),
                    "stderr_truncated": len(stderr) > MAX_CAPTURE_STREAM_CHARS,
                    "image_count": len(urls),
                    **_error_summary(error),
                },
            },
        )
        sink.record(artifact)
        return artifact_id
    except Exception:
        logger.warning("Unable to capture Python report artifact", exc_info=True)
        return None


def capture_generated_chart(
    *,
    source_artifact_id: str,
    uri: str,
    path: str,
    filename: str,
    artifact_sink: Optional[ArtifactSink] = None,
) -> Optional[str]:
    """Record one image emitted by a previously recorded Python execution."""
    sink = artifact_sink or get_current_artifact_sink()
    if sink is None:
        return None

    try:
        artifact_id = sink.context.new_artifact_id("chart")
        complete_sha256, complete_size = _sha256_file(path)
        artifact = ChartArtifact(
            artifact_id=artifact_id,
            execution=sink.context.execution_reference(action="code_interpreter.image"),
            input_artifact_ids=[source_artifact_id],
            created_at=datetime.now(timezone.utc),
            status="succeeded",
            duration_ms=0,
            name=filename,
            chart_type="image",
            spec={"source": "code_interpreter", "filename": filename},
            output=ArtifactContent(
                uri=uri,
                media_type="image/*",
                sha256=complete_sha256,
                size=complete_size,
            ),
        )
        sink.record(artifact)
        return artifact_id
    except Exception:
        logger.warning("Unable to capture chart report artifact", exc_info=True)
        return None


__all__ = [
    "MAX_CAPTURE_STREAM_CHARS",
    "MAX_CAPTURE_ERROR_CHARS",
    "MAX_SQL_PREVIEW_BYTES",
    "MAX_SQL_PREVIEW_ROWS",
    "capture_generated_chart",
    "capture_python_execution",
    "capture_sql_execution",
    "connector_database_name",
]
