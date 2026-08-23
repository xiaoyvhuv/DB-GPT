"""Tests for execution artifact capture context and in-memory collection."""

import asyncio
import importlib
from datetime import datetime

import pytest

from dbgpt.core.interface.report import ArtifactContent, SQLArtifact


def _capture_module():
    return importlib.import_module("dbgpt.core.interface.report_capture")


def _sql_artifact(*, artifact_id: str, conv_uid: str = "conv-1") -> SQLArtifact:
    capture = _capture_module()
    return SQLArtifact(
        artifact_id=artifact_id,
        execution=capture.ArtifactCaptureContext(
            conv_uid=conv_uid,
            run_id="capture-1",
        ).execution_reference(action="sql_query", step_id="step-1"),
        created_at=datetime(2026, 8, 23, 12, 0, 0),
        status="succeeded",
        duration_ms=1,
        sql="SELECT 1 AS value",
        columns=["value"],
        row_count=1,
        result=ArtifactContent(inline=[[1]]),
    )


def test_capture_context_generates_correlated_unique_identifiers() -> None:
    capture = _capture_module()
    values = iter(["artifact-token", "step-token", "second-step-token"])
    context = capture.ArtifactCaptureContext(
        conv_uid="conv-1",
        run_id="capture-1",
        message_id="message-1",
        task_id="task-1",
        id_factory=lambda: next(values),
    )

    assert context.new_artifact_id("sql") == "sql-artifact-token"
    first = context.execution_reference(action="sql_query")
    second = context.execution_reference(action="python")

    assert first.model_dump() == {
        "conv_uid": "conv-1",
        "step_id": "sql_query-step-token",
        "message_id": "message-1",
        "task_id": "task-1",
        "run_id": "capture-1",
        "action": "sql_query",
    }
    assert second.step_id == "python-second-step-token"


def test_bound_sink_restores_nested_context_and_isolates_async_tasks() -> None:
    capture = _capture_module()
    first = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )
    second = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-2", run_id="run-2")
    )

    assert capture.get_current_artifact_sink() is None
    with capture.bind_artifact_sink(first):
        assert capture.get_current_artifact_sink() is first
        with capture.bind_artifact_sink(second):
            assert capture.get_current_artifact_sink() is second
        assert capture.get_current_artifact_sink() is first
    assert capture.get_current_artifact_sink() is None

    async def bound_worker(sink):
        with capture.bind_artifact_sink(sink):
            await asyncio.sleep(0)
            return capture.get_current_artifact_sink()

    async def run_workers():
        return await asyncio.gather(bound_worker(first), bound_worker(second))

    assert asyncio.run(run_workers()) == [first, second]
    assert capture.get_current_artifact_sink() is None


def test_sink_rejects_duplicate_and_cross_conversation_artifacts() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="capture-1")
    )
    sink.record(_sql_artifact(artifact_id="sql-1"))

    with pytest.raises(ValueError, match="duplicate artifact id: sql-1"):
        sink.record(_sql_artifact(artifact_id="sql-1"))
    with pytest.raises(ValueError, match="belongs to conversation conv-2"):
        sink.record(_sql_artifact(artifact_id="sql-2", conv_uid="conv-2"))


def test_sink_rejects_cross_run_and_mutated_artifacts() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="capture-1")
    )
    wrong_run = _sql_artifact(artifact_id="sql-other-run")
    wrong_run.execution.run_id = "run-2"

    with pytest.raises(ValueError, match="belongs to run run-2"):
        sink.record(wrong_run)

    mutated = _sql_artifact(artifact_id="sql-mutated")
    mutated.artifact_id = " "
    with pytest.raises(ValueError, match="must not be blank"):
        sink.record(mutated)

    invalid_created_at = _sql_artifact(artifact_id="sql-invalid-created-at")
    invalid_created_at.created_at = None
    with pytest.raises(ValueError, match="created_at"):
        sink.record(invalid_created_at)


def test_sink_rejects_artifact_without_capture_outcome() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="capture-1")
    )
    legacy = _sql_artifact(artifact_id="sql-legacy")
    legacy.status = None
    legacy.duration_ms = None

    with pytest.raises(ValueError, match="status and duration"):
        sink.record(legacy)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda artifact: setattr(artifact, "artifact_id", "a" * 129),
            "artifact_id exceeds 128",
        ),
        (
            lambda artifact: setattr(artifact, "input_artifact_ids", ["a" * 129]),
            "input_artifact_id exceeds 128",
        ),
        (
            lambda artifact: setattr(artifact.execution, "conv_uid", "c" * 256),
            "execution.conv_uid exceeds 255",
        ),
        (
            lambda artifact: setattr(artifact.execution, "step_id", "s" * 256),
            "execution.step_id exceeds 255",
        ),
        (
            lambda artifact: setattr(artifact.execution, "message_id", "m" * 256),
            "execution.message_id exceeds 255",
        ),
        (
            lambda artifact: setattr(artifact.execution, "task_id", "t" * 65),
            "execution.task_id exceeds 64",
        ),
        (
            lambda artifact: setattr(artifact.execution, "run_id", "r" * 65),
            "execution.run_id exceeds 64",
        ),
    ],
)
def test_sink_rejects_unpersistable_mutated_artifact_coordinates(
    mutate, message: str
) -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="capture-1")
    )
    artifact = _sql_artifact(artifact_id="sql-mutated")
    mutate(artifact)

    with pytest.raises(ValueError, match=message):
        sink.record(artifact)


def test_sink_requires_dependencies_to_be_recorded_first() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="capture-1")
    )
    dependent = _sql_artifact(artifact_id="sql-dependent")
    dependent.input_artifact_ids = ["sql-source"]

    with pytest.raises(ValueError, match="unknown input artifact sql-source"):
        sink.record(dependent)

    sink.record(_sql_artifact(artifact_id="sql-source"))
    sink.record(dependent)

    assert [item.artifact_id for item in sink.snapshot()] == [
        "sql-source",
        "sql-dependent",
    ]


def test_sink_rejects_self_dependency() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="capture-1")
    )
    artifact = _sql_artifact(artifact_id="sql-self")
    artifact.input_artifact_ids = ["sql-self"]

    with pytest.raises(ValueError, match="cannot depend on itself"):
        sink.record(artifact)


@pytest.mark.parametrize(
    "context_kwargs",
    [
        {"conv_uid": "c" * 256},
        {"conv_uid": "conv-1", "run_id": "r" * 65},
        {"conv_uid": "conv-1", "message_id": "m" * 256},
        {"conv_uid": "conv-1", "task_id": "t" * 65},
    ],
)
def test_capture_context_rejects_unpersistable_coordinates(
    context_kwargs: dict,
) -> None:
    capture = _capture_module()

    with pytest.raises(ValueError, match="exceeds"):
        capture.ArtifactCaptureContext(**context_kwargs)


def test_capture_context_rejects_unpersistable_generated_identifiers() -> None:
    capture = _capture_module()
    context = capture.ArtifactCaptureContext(
        conv_uid="conv-1",
        id_factory=lambda: "x" * 128,
    )

    with pytest.raises(ValueError, match="artifact_id exceeds 128"):
        context.new_artifact_id("sql")
    with pytest.raises(ValueError, match="step_id exceeds 255"):
        context.execution_reference(action="a" * 128)


def test_sink_owns_deep_copies_on_record_and_snapshot() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="capture-1")
    )
    artifact = _sql_artifact(artifact_id="sql-1")
    artifact.metadata["summary"] = {"rows": 1}
    sink.record(artifact)

    artifact.metadata["summary"]["rows"] = 999
    first_snapshot = sink.snapshot()
    first_snapshot[0].metadata["summary"]["rows"] = 888

    assert sink.snapshot()[0].metadata == {"summary": {"rows": 1}}


def test_canonical_hash_is_order_independent_and_rejects_non_finite_json() -> None:
    capture = _capture_module()

    assert capture.sha256_json({"b": 2, "a": 1}) == (
        "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    )
    assert capture.sha256_json({"a": 1, "b": 2}) == capture.sha256_json(
        {"b": 2, "a": 1}
    )
    assert capture.sha256_text("上海") == (
        "4906a49adc5c472b34483976104ec1d76992745367cc22d5fbc2a4db8e8e1ed7"
    )

    with pytest.raises(ValueError, match="JSON-compatible"):
        capture.sha256_json({"value": float("nan")})


@pytest.mark.parametrize(
    "value",
    [
        (1, 2),
        {1: "integer keys are lossy"},
        {"value": float("inf")},
        {"value": b"bytes"},
    ],
)
def test_canonical_hash_rejects_values_that_json_would_coerce(value) -> None:
    capture = _capture_module()

    with pytest.raises(ValueError, match="JSON-compatible"):
        capture.sha256_json(value)


def test_capture_api_is_available_from_core_public_api() -> None:
    import dbgpt.core as core

    public_names = {
        "ArtifactCaptureContext",
        "ArtifactSink",
        "InMemoryArtifactSink",
        "bind_artifact_sink",
        "get_current_artifact_sink",
        "sha256_bytes",
        "sha256_json",
        "sha256_text",
    }

    assert all(hasattr(core, name) for name in public_names)
