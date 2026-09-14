"""Tests for the report evidence manifest exposed to the report author."""

import json
from datetime import datetime, timezone

import pytest

from dbgpt.core.interface.report import (
    ArtifactContent,
    ChartArtifact,
    PythonArtifact,
    ReferenceArtifact,
    SQLArtifact,
)
from dbgpt.core.interface.report_capture import (
    ArtifactCaptureContext,
    InMemoryArtifactSink,
)
from dbgpt_app.openapi.api_v1.tools.report_evidence import (
    make_report_evidence_manifest,
)


@pytest.mark.asyncio
async def test_manifest_serializes_mixed_artifacts_by_discriminated_type() -> None:
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-1")
    )
    now = datetime.now(timezone.utc)
    sql = SQLArtifact(
        artifact_id="sql-1",
        execution=sink.context.execution_reference(action="sql_query"),
        created_at=now,
        status="succeeded",
        duration_ms=2,
        sql="SELECT gmv FROM sales",
        database="sales",
        columns=["gmv"],
        row_count=1,
        result=ArtifactContent(inline={"columns": ["gmv"], "rows": [[42]]}),
    )
    python = PythonArtifact(
        artifact_id="python-1",
        execution=sink.context.execution_reference(action="code_interpreter"),
        input_artifact_ids=[sql.artifact_id],
        created_at=now,
        status="succeeded",
        duration_ms=3,
        code="print(42)",
        exit_code=0,
        stdout="42",
        stderr="",
    )
    chart = ChartArtifact(
        artifact_id="chart-1",
        execution=sink.context.execution_reference(action="code_interpreter.image"),
        input_artifact_ids=[python.artifact_id],
        created_at=now,
        status="succeeded",
        duration_ms=0,
        chart_type="line",
        output=ArtifactContent(uri="/images/chart.png"),
    )
    reference = ReferenceArtifact(
        artifact_id="reference-1",
        execution=sink.context.execution_reference(action="knowledge_retrieve"),
        created_at=now,
        status="succeeded",
        duration_ms=1,
        reference_type="knowledge",
        source_id="chunk-1",
        source_name="Metric handbook",
        locator={"page": 7},
        excerpt="GMV definition",
    )
    for artifact in (sql, python, chart, reference):
        sink.record(artifact)

    payload = json.loads(
        await make_report_evidence_manifest({"_report_artifact_sink": sink})()
    )

    assert payload["status"] == "ok"
    assert payload["diagnostics"]["counts_by_type"] == {
        "chart": 1,
        "python": 1,
        "reference": 1,
        "sql": 1,
    }
    by_type = {item["type"]: item for item in payload["artifacts"]}
    assert by_type["sql"]["columns"] == ["gmv"]
    assert by_type["python"]["stdout_preview"] == "42"
    assert by_type["chart"]["input_artifact_ids"] == ["python-1"]
    assert by_type["reference"]["locator"] == {"page": 7}
    assert payload["binding_examples"][0]["html"] == (
        '<section data-dbgpt-artifacts="sql-1" '
        'data-dbgpt-evidence-label="指标名称">'
        '<strong data-dbgpt-artifacts="sql-1">数值</strong></section>'
    )
    assert "chart-1" in payload["binding_examples"][1]["html"]


@pytest.mark.asyncio
async def test_manifest_distinguishes_unavailable_and_empty_capture() -> None:
    unavailable = json.loads(await make_report_evidence_manifest({})())
    sink = InMemoryArtifactSink(
        ArtifactCaptureContext(conv_uid="conv-1", run_id="run-empty")
    )
    empty = json.loads(
        await make_report_evidence_manifest({"_report_artifact_sink": sink})()
    )

    assert unavailable["status"] == "capture_unavailable"
    assert empty["status"] == "empty"
    assert empty["diagnostics"]["captured_count"] == 0
