"""Tests for execution artifact capture context and in-memory collection."""

import asyncio
import importlib
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from dbgpt.core.interface.report import ArtifactContent, ReferenceArtifact, SQLArtifact


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


def test_reference_capture_is_bounded_and_keeps_complete_content_hash() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )
    excerpt = "knowledge evidence" * 1_000

    with capture.bind_artifact_sink(sink):
        artifact_id = capture.capture_reference_artifact(
            reference_type="knowledge",
            source_id="chunk-7",
            action="knowledge.retrieve",
            duration_ms=12,
            source_name="guide.pdf",
            uri="kb://space-1/document-3/chunk-7",
            locator={"space_id": 1, "document_id": 3, "chunk_id": 7, "page": 9},
            excerpt=excerpt,
            score=0.91,
            metadata={"query_sha256": capture.sha256_text("report schema")},
        )

    artifact = sink.snapshot()[0]
    assert artifact_id == artifact.artifact_id
    assert isinstance(artifact, ReferenceArtifact)
    assert artifact.reference_type == "knowledge"
    assert artifact.locator == {
        "space_id": 1,
        "document_id": 3,
        "chunk_id": 7,
        "page": 9,
    }
    assert artifact.excerpt == excerpt[: capture.MAX_REFERENCE_EXCERPT_CHARS]
    assert artifact.metadata["content_summary"] == {
        "excerpt_chars": len(excerpt),
        "excerpt_sha256": capture.sha256_text(excerpt),
        "excerpt_truncated": True,
    }


def test_reference_capture_is_noop_without_bound_sink() -> None:
    capture = _capture_module()

    assert (
        capture.capture_reference_artifact(
            reference_type="web",
            source_id="https://example.com",
            action="web.search",
            duration_ms=0,
        )
        is None
    )


def test_reference_capture_normalizes_non_finite_score() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )

    with capture.bind_artifact_sink(sink):
        capture.capture_reference_artifact(
            reference_type="knowledge",
            source_id="chunk-1",
            action="knowledge.retrieve",
            duration_ms=1,
            score=float("nan"),
        )

    assert sink.snapshot()[0].score is None


def test_reference_capture_redacts_url_credentials() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )
    url = (
        "https://user:password@example.com/report?section=gmv&"
        "access_token=secret-value#private"
    )

    with capture.bind_artifact_sink(sink):
        capture.capture_reference_artifact(
            reference_type="web",
            source_id="web-1",
            action="web.fetch",
            duration_ms=1,
            source_name=url,
            uri=url,
            locator={"url": url},
        )

    artifact = sink.snapshot()[0]
    assert artifact.uri == (
        "https://example.com/report?section=gmv&access_token=%5BREDACTED%5D"
    )
    assert artifact.locator["url"] == artifact.uri
    assert artifact.source_name == artifact.uri
    assert "password" not in artifact.model_dump_json()
    assert "secret-value" not in artifact.model_dump_json()


def test_reference_capture_redacts_sensitive_query_name_families() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )
    url = (
        "https://example.com/report?client_secret=a&refresh_token=b&id_token=c&"
        "x-api-key=d&access_key=e&secret_key=f&private_key=g&"
        "AWSAccessKeyId=h&bearerToken=i&dbPassword=j&safe=value"
    )

    with capture.bind_artifact_sink(sink):
        capture.capture_reference_artifact(
            reference_type="web",
            source_id="web-1",
            action="web.fetch",
            duration_ms=1,
            uri=url,
            locator={"requested_url": url, "final_url": url},
        )

    serialized = sink.snapshot()[0].model_dump_json()
    for secret in "abcdefghij":
        assert f"={secret}" not in serialized
    assert "safe=value" in serialized
    assert serialized.count("%5BREDACTED%5D") == 30


def test_reference_capture_hashes_excerpt_parts_without_full_join() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )
    parts = ["a" * 3_000, "b" * 3_000]

    with capture.bind_artifact_sink(sink):
        capture.capture_reference_artifact(
            reference_type="knowledge",
            source_id="document-1",
            action="knowledge.cat",
            duration_ms=1,
            excerpt_parts=iter(parts),
            excerpt_separator="\n",
        )

    artifact = sink.snapshot()[0]
    complete = "\n".join(parts)
    assert artifact.excerpt == complete[: capture.MAX_REFERENCE_EXCERPT_CHARS]
    assert artifact.metadata["content_summary"] == {
        "excerpt_chars": len(complete),
        "excerpt_sha256": capture.sha256_text(complete),
        "excerpt_truncated": True,
    }


def test_tabular_summary_is_lossless_and_bounds_preview() -> None:
    capture = _capture_module()
    precise_time = datetime(2026, 8, 23, 12, 0, 0, 123456, tzinfo=timezone.utc)
    rows = [
        [Decimal("1.2300"), precise_time, b"raw", float("nan")],
        [Decimal("2.0"), precise_time, b"more", float("inf")],
    ]

    preview, complete_hash, row_count, truncated = capture.summarize_tabular_artifact(
        ["decimal", "time", "bytes", "special"],
        rows,
        max_preview_rows=1,
        max_preview_bytes=10_000,
    )

    assert row_count == 2
    assert truncated
    assert preview["rows"] == [
        [
            {"$decimal": "1.2300"},
            {"$datetime": precise_time.isoformat()},
            {"$bytes_base64": "cmF3"},
            {"$number": "NaN"},
        ]
    ]
    complete = {
        "columns": ["decimal", "time", "bytes", "special"],
        "rows": [
            preview["rows"][0],
            [
                {"$decimal": "2.0"},
                {"$datetime": precise_time.isoformat()},
                {"$bytes_base64": "bW9yZQ=="},
                {"$number": "Infinity"},
            ],
        ],
    }
    assert complete_hash == capture.sha256_json(complete)


def test_tabular_summary_normalizes_sqlalchemy_style_rows() -> None:
    capture = _capture_module()

    class Row:
        _mapping = {"category": "health_beauty", "revenue": Decimal("1253993.86")}

    preview, _, row_count, truncated = capture.summarize_tabular_artifact(
        ["category", "revenue"], [Row()], max_preview_rows=10, max_preview_bytes=10_000
    )

    assert row_count == 1
    assert not truncated
    assert preview["rows"] == [
        {"category": "health_beauty", "revenue": {"$decimal": "1253993.86"}}
    ]


def test_text_hash_matches_utf8_hash_for_large_unicode_payload() -> None:
    capture = _capture_module()
    value = "证据-hash-" * 100_000

    assert capture.sha256_text(value) == capture.sha256_bytes(value.encode("utf-8"))


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


def test_in_memory_sink_exposes_cheap_artifact_count() -> None:
    capture = _capture_module()
    sink = capture.InMemoryArtifactSink(
        capture.ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )

    assert sink.artifact_count == 0
    with capture.bind_artifact_sink(sink):
        capture.capture_reference_artifact(
            reference_type="model",
            source_id="model-1",
            action="model.invoke",
            duration_ms=1,
            metadata={"provider": "test"},
        )

    assert sink.artifact_count == 1
