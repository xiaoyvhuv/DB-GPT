"""Tests for deterministic assembly of captured artifacts into reports."""

from datetime import datetime, timezone

import pytest

from dbgpt.core.interface.report import (
    ArtifactContent,
    ChartArtifact,
    ChartBlock,
    ExecutionReference,
    InsightBlock,
    MetricBlock,
    ReferenceArtifact,
    SQLArtifact,
    TableBlock,
    TextBlock,
)
from dbgpt.core.interface.report_assembly import ReportAssembler

CREATED_AT = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
EXECUTION = ExecutionReference(conv_uid="conv-1", step_id="step-1", run_id="turn-1")


def _sql(
    artifact_id: str,
    columns: list[str],
    rows: list[list[object]],
) -> SQLArtifact:
    return SQLArtifact(
        artifact_id=artifact_id,
        execution=EXECUTION,
        created_at=CREATED_AT,
        status="succeeded",
        duration_ms=10,
        sql="SELECT metric FROM sales",
        columns=columns,
        row_count=len(rows),
        result=ArtifactContent(inline={"columns": columns, "rows": rows}),
    )


def test_assembler_maps_real_artifact_shapes_to_native_blocks() -> None:
    scalar = _sql("sql-total", ["gmv"], [[1186000]])
    table = _sql(
        "sql-trend",
        ["day", "gmv"],
        [["2026-08-23", 100], ["2026-08-24", 120]],
    )
    reference = ReferenceArtifact(
        artifact_id="reference-market",
        execution=EXECUTION,
        created_at=CREATED_AT,
        status="succeeded",
        duration_ms=5,
        reference_type="knowledge",
        source_id="chunk-7",
        source_name="Sales handbook",
        locator={"page": 7},
        excerpt="GMV is recognized when an order is paid.",
    )
    chart = ChartArtifact(
        artifact_id="chart-trend",
        execution=EXECUTION,
        input_artifact_ids=[table.artifact_id, reference.artifact_id],
        created_at=CREATED_AT,
        status="succeeded",
        duration_ms=1,
        chart_type="line",
        spec={"x": "day", "y": "gmv"},
        output=ArtifactContent(uri="dbgpt-fs://reports/chart-trend"),
    )

    report = ReportAssembler().assemble(
        report_id="report-sales",
        title="Sales analysis",
        conv_uid="conv-1",
        final_content="GMV reached 1,186,000 and increased on August 24.",
        artifacts=[scalar, table, chart, reference],
        supporting_artifact_ids=[
            "sql-total",
            "sql-trend",
            "chart-trend",
            "reference-market",
        ],
        created_at=CREATED_AT,
    )

    assert report.schema_version == 2
    assert report.artifacts == [scalar, table, chart, reference]
    summary = report.sections[0].blocks[0]
    assert isinstance(summary, InsightBlock)
    assert {item.artifact_id for item in summary.evidence} == {
        "sql-total",
        "sql-trend",
        "chart-trend",
        "reference-market",
    }
    blocks = report.sections[1].blocks
    assert [type(block) for block in blocks] == [
        MetricBlock,
        TableBlock,
        ChartBlock,
    ]
    metric = blocks[0]
    assert metric.value == 1186000
    assert metric.title == "gmv"
    assert metric.evidence[0].selector == {
        "kind": "cell",
        "row": 0,
        "column": "gmv",
    }
    assert blocks[1].data_artifact_id == "sql-trend"
    assert [item.artifact_id for item in blocks[2].evidence] == [
        "chart-trend",
        "sql-trend",
        "reference-market",
    ]
    assert blocks[2].evidence[2].selector == {"page": 7}


def test_assembler_retains_failed_artifacts_without_fabricated_blocks() -> None:
    failed = SQLArtifact(
        artifact_id="sql-failed",
        execution=EXECUTION,
        created_at=CREATED_AT,
        status="failed",
        duration_ms=4,
        error="database unavailable",
        sql="SELECT 1",
    )

    report = ReportAssembler().assemble(
        report_id="report-failed",
        title="Failed analysis",
        conv_uid="conv-1",
        final_content="The query could not be completed.",
        artifacts=[failed],
        created_at=CREATED_AT,
    )

    assert report.artifacts == [failed]
    assert report.sections == []
    assert report.metadata["task_summary"] == "The query could not be completed."


def test_assembler_does_not_render_unbound_summary_as_report_block() -> None:
    reference = ReferenceArtifact(
        artifact_id="reference-unused",
        execution=EXECUTION,
        created_at=CREATED_AT,
        status="succeeded",
        duration_ms=1,
        reference_type="web",
        source_id="web-unused",
        excerpt="Unused search result",
    )

    report = ReportAssembler().assemble(
        report_id="report-text",
        title="Plain answer",
        conv_uid="conv-1",
        final_content="A response without an explicit citation.",
        artifacts=[reference],
        created_at=CREATED_AT,
    )

    assert report.sections == []
    assert report.metadata["task_summary"] == "A response without an explicit citation."


def test_assembler_rejects_failed_explicit_conclusion_support() -> None:
    failed = SQLArtifact(
        artifact_id="sql-failed-support",
        execution=EXECUTION,
        created_at=CREATED_AT,
        status="failed",
        duration_ms=1,
        error="query failed",
        sql="SELECT 1",
    )

    with pytest.raises(ValueError, match="supporting artifact must have succeeded"):
        ReportAssembler().assemble(
            report_id="report-failed-support",
            title="Failed support",
            conv_uid="conv-1",
            final_content="A conclusion.",
            artifacts=[failed],
            supporting_artifact_ids=[failed.artifact_id],
            created_at=CREATED_AT,
        )


def test_assembler_reports_missing_chart_dependency_as_integrity_error() -> None:
    chart = ChartArtifact(
        artifact_id="chart-orphan",
        execution=EXECUTION,
        input_artifact_ids=["sql-missing"],
        created_at=CREATED_AT,
        status="succeeded",
        duration_ms=1,
        chart_type="line",
        output=ArtifactContent(uri="dbgpt-fs://reports/chart-orphan"),
    )

    with pytest.raises(ValueError, match="unknown input artifact sql-missing"):
        ReportAssembler().assemble(
            report_id="report-orphan",
            title="Orphan chart",
            conv_uid="conv-1",
            final_content="A chart was generated.",
            artifacts=[chart],
            created_at=CREATED_AT,
        )
