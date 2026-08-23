"""Tests for the core report and evidence wire protocol."""

from copy import deepcopy
from datetime import datetime

import pytest
from pydantic import ValidationError

from dbgpt.core.interface.report import (
    ArtifactContent,
    ChartArtifact,
    ChartBlock,
    EvidenceReference,
    ExecutionReference,
    InsightBlock,
    MetricBlock,
    PythonArtifact,
    ReferenceArtifact,
    ReportDocument,
    SQLArtifact,
    TableBlock,
    TextBlock,
)

EXECUTION = ExecutionReference(
    conv_uid="conv-1",
    step_id="step-1",
    message_id="message-1",
    task_id="task-1",
    run_id="run-1",
    action="sql_query",
)
CREATED_AT = datetime(2026, 8, 23, 9, 30, 0)


def test_sql_artifact_preserves_executed_query_result() -> None:
    artifact = SQLArtifact(
        artifact_id="sql-sales",
        execution=EXECUTION,
        created_at=CREATED_AT,
        sql="SELECT day, gmv FROM sales",
        database="commerce",
        display_type="response_table",
        columns=["day", "gmv"],
        row_count=1,
        result=ArtifactContent(inline=[{"day": "2026-08-20", "gmv": 1186000}]),
    )

    assert artifact.type == "sql"
    assert artifact.result.inline == [{"day": "2026-08-20", "gmv": 1186000}]
    assert artifact.execution.step_id == "step-1"


def test_python_and_chart_artifacts_preserve_execution_lineage() -> None:
    python = PythonArtifact(
        artifact_id="python-delta",
        execution=EXECUTION,
        input_artifact_ids=["sql-sales"],
        created_at=CREATED_AT,
        code="delta = current - previous",
        exit_code=0,
        stdout="delta=-120000",
        result=ArtifactContent(inline={"delta": -120000}),
    )
    chart = ChartArtifact(
        artifact_id="chart-sales",
        execution=EXECUTION,
        input_artifact_ids=["python-delta"],
        created_at=CREATED_AT,
        chart_type="line",
        spec={"x": "day", "y": "gmv"},
        output=ArtifactContent(uri="dbgpt-fs://local/reports/chart-sales"),
    )

    assert python.language == "python"
    assert python.input_artifact_ids == ["sql-sales"]
    assert chart.input_artifact_ids == ["python-delta"]


def test_reference_artifact_preserves_current_knowledge_citation() -> None:
    artifact = ReferenceArtifact(
        artifact_id="knowledge-revenue",
        execution=EXECUTION,
        created_at=CREATED_AT,
        reference_type="knowledge",
        source_id="chunk-1",
        source_name="annual-report.md",
        uri="https://example.test/annual-report",
        locator={"chunkIndex": 1, "path": "reports/annual-report.md"},
        excerpt="Revenue grew by 20 percent.",
        score=0.91,
    )

    assert artifact.source_id == "chunk-1"
    assert artifact.locator["chunkIndex"] == 1
    assert artifact.score == 0.91


def test_artifact_content_requires_inline_or_uri() -> None:
    with pytest.raises(ValidationError, match="inline or uri"):
        ArtifactContent()


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (ExecutionReference, {"conv_uid": " ", "step_id": "step-1"}),
        (ArtifactContent, {"uri": " "}),
        (
            SQLArtifact,
            {
                "artifact_id": " ",
                "execution": EXECUTION,
                "created_at": CREATED_AT,
                "sql": "SELECT 1",
                "result": {"inline": [{"value": 1}]},
            },
        ),
    ],
)
def test_protocol_rejects_blank_required_text(model, kwargs) -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        model(**kwargs)


def test_protocol_rejects_undeclared_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExecutionReference(conv_uid="conv-1", step_id="step-1", unknown=True)


def _valid_report_payload() -> dict:
    return {
        "schema_version": 1,
        "report_id": "report-sales",
        "title": "Shanghai GMV analysis",
        "conv_uid": "conv-1",
        "created_at": "2026-08-23T09:30:00",
        "artifacts": [
            {
                "type": "sql",
                "artifact_id": "sql-sales",
                "execution": {"conv_uid": "conv-1", "step_id": "step-1"},
                "created_at": "2026-08-23T09:30:00",
                "sql": "SELECT day, gmv FROM sales",
                "columns": ["day", "gmv"],
                "row_count": 1,
                "result": {"inline": [{"day": "2026-08-20", "gmv": 1186000}]},
            },
            {
                "type": "python",
                "artifact_id": "python-delta",
                "execution": {"conv_uid": "conv-1", "step_id": "step-2"},
                "input_artifact_ids": ["sql-sales"],
                "created_at": "2026-08-23T09:31:00",
                "code": "delta = current - previous",
                "exit_code": 0,
                "result": {"inline": {"delta": -120000}},
            },
            {
                "type": "chart",
                "artifact_id": "chart-sales",
                "execution": {"conv_uid": "conv-1", "step_id": "step-3"},
                "input_artifact_ids": ["python-delta"],
                "created_at": "2026-08-23T09:32:00",
                "chart_type": "line",
                "spec": {"x": "day", "y": "gmv"},
                "output": {"uri": "dbgpt-fs://local/reports/chart-sales"},
            },
        ],
        "sections": [
            {
                "section_id": "summary",
                "title": "Summary",
                "blocks": [
                    {
                        "type": "metric",
                        "block_id": "metric-gmv",
                        "title": "GMV",
                        "value": 1186000,
                        "display_value": "1.186M",
                        "unit": "CNY",
                        "evidence": [
                            {
                                "artifact_id": "sql-sales",
                                "role": "source",
                                "selector": {"column": "gmv", "row": 0},
                                "target_selector": {"marker_id": "gmv-value"},
                            }
                        ],
                    },
                    {
                        "type": "table",
                        "block_id": "table-gmv",
                        "title": "GMV rows",
                        "data_artifact_id": "sql-sales",
                    },
                    {
                        "type": "chart",
                        "block_id": "chart-gmv",
                        "title": "GMV trend",
                        "chart_artifact_id": "chart-sales",
                    },
                    {
                        "type": "insight",
                        "block_id": "insight-delta",
                        "content": "GMV decreased by 120000 CNY.",
                        "evidence": [
                            {
                                "artifact_id": "python-delta",
                                "role": "calculation",
                            }
                        ],
                    },
                    {
                        "type": "text",
                        "block_id": "text-method",
                        "content": "The report uses executed data-assistant steps.",
                    },
                ],
            }
        ],
    }


def test_report_round_trip_preserves_five_block_types_and_artifacts() -> None:
    report = ReportDocument.model_validate(_valid_report_payload())
    restored = ReportDocument.model_validate_json(report.model_dump_json())

    assert isinstance(restored.artifacts[0], SQLArtifact)
    assert isinstance(restored.artifacts[1], PythonArtifact)
    assert isinstance(restored.artifacts[2], ChartArtifact)
    assert isinstance(restored.sections[0].blocks[0], MetricBlock)
    assert isinstance(restored.sections[0].blocks[1], TableBlock)
    assert isinstance(restored.sections[0].blocks[2], ChartBlock)
    assert isinstance(restored.sections[0].blocks[3], InsightBlock)
    assert isinstance(restored.sections[0].blocks[4], TextBlock)


def test_evidence_separates_source_and_report_target_selectors() -> None:
    report = ReportDocument.model_validate(_valid_report_payload())
    evidence = report.sections[0].blocks[0].evidence[0]

    assert isinstance(evidence, EvidenceReference)
    assert evidence.selector == {"column": "gmv", "row": 0}
    assert evidence.target_selector == {"marker_id": "gmv-value"}


def test_report_json_schema_exposes_artifact_and_block_discriminators() -> None:
    schema = ReportDocument.model_json_schema()

    artifact_items = schema["properties"]["artifacts"]["items"]
    block_items = schema["$defs"]["ReportSection"]["properties"]["blocks"]["items"]
    assert artifact_items["discriminator"]["propertyName"] == "type"
    assert block_items["discriminator"]["propertyName"] == "type"


@pytest.mark.parametrize("block_type", ["metric", "insight"])
def test_derived_blocks_require_evidence(block_type: str) -> None:
    blocks = _valid_report_payload()["sections"][0]["blocks"]
    block = next(item for item in blocks if item["type"] == block_type)
    block["evidence"] = []
    model = MetricBlock if block_type == "metric" else InsightBlock

    with pytest.raises(ValidationError, match="requires evidence"):
        model.model_validate(block)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["artifacts"].append(
                deepcopy(payload["artifacts"][0])
            ),
            "duplicate artifact id",
        ),
        (
            lambda payload: payload["sections"].append(
                deepcopy(payload["sections"][0])
            ),
            "duplicate section id",
        ),
        (
            lambda payload: payload["sections"][0]["blocks"].append(
                deepcopy(payload["sections"][0]["blocks"][0])
            ),
            "duplicate block id",
        ),
    ],
)
def test_report_rejects_duplicate_graph_ids(mutate, message: str) -> None:
    payload = _valid_report_payload()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        ReportDocument.model_validate(payload)


def test_report_rejects_dangling_evidence_reference() -> None:
    payload = _valid_report_payload()
    payload["sections"][0]["blocks"][0]["evidence"][0]["artifact_id"] = "missing"

    with pytest.raises(ValidationError, match="unknown artifact missing"):
        ReportDocument.model_validate(payload)


def test_report_rejects_dangling_artifact_input() -> None:
    payload = _valid_report_payload()
    payload["artifacts"][1]["input_artifact_ids"] = ["missing"]

    with pytest.raises(ValidationError, match="unknown input artifact missing"):
        ReportDocument.model_validate(payload)


@pytest.mark.parametrize(
    "dependencies",
    [
        {"sql-sales": ["sql-sales"]},
        {
            "sql-sales": ["chart-sales"],
            "python-delta": ["sql-sales"],
            "chart-sales": ["python-delta"],
        },
    ],
)
def test_report_rejects_artifact_dependency_cycles(dependencies: dict) -> None:
    payload = _valid_report_payload()
    for artifact in payload["artifacts"]:
        if artifact["artifact_id"] in dependencies:
            artifact["input_artifact_ids"] = dependencies[artifact["artifact_id"]]

    with pytest.raises(ValidationError, match="artifact dependency cycle"):
        ReportDocument.model_validate(payload)


def test_chart_block_requires_chart_artifact() -> None:
    payload = _valid_report_payload()
    payload["sections"][0]["blocks"][2]["chart_artifact_id"] = "sql-sales"

    with pytest.raises(ValidationError, match="requires a chart artifact"):
        ReportDocument.model_validate(payload)


def test_table_block_requires_sql_or_python_artifact() -> None:
    payload = _valid_report_payload()
    payload["sections"][0]["blocks"][1]["data_artifact_id"] = "chart-sales"

    with pytest.raises(ValidationError, match="requires a SQL or Python artifact"):
        ReportDocument.model_validate(payload)


def test_chart_artifact_requires_sql_or_python_lineage() -> None:
    payload = _valid_report_payload()
    payload["artifacts"].append(
        {
            "type": "reference",
            "artifact_id": "web-source",
            "execution": {"conv_uid": "conv-1", "step_id": "step-4"},
            "created_at": "2026-08-23T09:33:00",
            "reference_type": "web",
            "source_id": "https://example.test/source",
        }
    )
    payload["artifacts"][2]["input_artifact_ids"] = ["web-source"]

    with pytest.raises(ValidationError, match="requires SQL or Python lineage"):
        ReportDocument.model_validate(payload)


def test_report_protocol_is_available_from_core_public_api() -> None:
    import dbgpt.core as core

    public_names = {
        "Artifact",
        "ArtifactContent",
        "ChartArtifact",
        "ChartBlock",
        "EvidenceReference",
        "ExecutionReference",
        "InsightBlock",
        "MetricBlock",
        "PythonArtifact",
        "ReferenceArtifact",
        "ReportBlock",
        "ReportDocument",
        "ReportSection",
        "SQLArtifact",
        "TableBlock",
        "TextBlock",
    }

    assert all(hasattr(core, name) for name in public_names)
    assert core.ReportDocument is ReportDocument
    assert core.SQLArtifact is SQLArtifact
