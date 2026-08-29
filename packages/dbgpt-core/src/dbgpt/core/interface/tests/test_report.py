"""Tests for the core report and evidence wire protocol."""

from copy import deepcopy
from datetime import datetime

import pytest
from pydantic import ValidationError

from dbgpt.core.interface.evidence_packet import EvidenceHandle, EvidencePacket
from dbgpt.core.interface.metric import BetterDirection, MetricDefinition
from dbgpt.core.interface.report import (
    ArtifactContent,
    ChartArtifact,
    ChartBlock,
    Claim,
    ClaimSegment,
    ClaimVerificationResult,
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
    TextSegment,
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


def test_metric_definition_describes_percent_tolerance_and_trend_rule() -> None:
    definition = MetricDefinition(
        metric_key="overdue_rate",
        display_name="Overdue rate",
        unit="ratio",
        better_direction=BetterDirection.LOWER,
        absolute_tolerance=0.00005,
        significant_change=0.01,
    )

    assert definition.better_direction == BetterDirection.LOWER
    assert definition.significant_change == 0.01


def test_verification_result_keeps_machine_readable_failure_details() -> None:
    result = ClaimVerificationResult(
        claim_id="claim-rate",
        status="insufficient_evidence",
        verification_method="business_rule",
        evidence_artifact_ids=[],
        verifier_name="trend-rule",
        verifier_version="1",
        reason_code="INSUFFICIENT_RULE",
        details={"metric_key": "overdue_rate"},
        latency_ms=7,
    )

    assert result.reason_code == "INSUFFICIENT_RULE"
    assert result.latency_ms == 7


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


def test_failed_sql_artifact_preserves_failure_without_fabricated_result() -> None:
    artifact = SQLArtifact(
        artifact_id="sql-failed",
        execution=EXECUTION,
        created_at=CREATED_AT,
        status="failed",
        duration_ms=17,
        error="database unavailable",
        sql="SELECT 1",
        result=None,
    )

    assert artifact.status == "failed"
    assert artifact.duration_ms == 17
    assert artifact.error == "database unavailable"
    assert artifact.result is None


def test_successful_sql_artifact_requires_a_real_result() -> None:
    with pytest.raises(
        ValidationError, match="successful SQL artifact requires result"
    ):
        SQLArtifact(
            artifact_id="sql-missing-result",
            execution=EXECUTION,
            created_at=CREATED_AT,
            sql="SELECT 1",
            result=None,
        )


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


def test_timed_out_python_artifact_preserves_real_process_state() -> None:
    artifact = PythonArtifact(
        artifact_id="python-timeout",
        execution=EXECUTION,
        created_at=CREATED_AT,
        status="timed_out",
        duration_ms=60_000,
        error="execution timed out",
        code="while True: pass",
        exit_code=None,
        stdout="",
        stderr="",
    )

    assert artifact.status == "timed_out"
    assert artifact.exit_code is None
    assert artifact.stderr == ""


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "succeeded", "exit_code": None},
        {"status": "succeeded", "exit_code": 1},
        {"status": "succeeded", "exit_code": 0, "error": "unexpected"},
        {"status": "timed_out", "exit_code": 0, "error": "timed out"},
        {"status": "failed", "exit_code": 1, "error": None},
    ],
)
def test_python_artifact_rejects_inconsistent_process_state(kwargs: dict) -> None:
    with pytest.raises(ValidationError, match="artifact"):
        PythonArtifact(
            artifact_id="python-invalid",
            execution=EXECUTION,
            created_at=CREATED_AT,
            code="print(1)",
            **kwargs,
        )


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


def _valid_v2_report_payload() -> dict:
    payload = _valid_report_payload()
    payload["schema_version"] = 2
    for artifact in payload["artifacts"]:
        artifact["status"] = "succeeded"
        artifact["duration_ms"] = 1
    return payload


def _valid_v3_report_payload() -> dict:
    payload = _valid_v2_report_payload()
    payload["schema_version"] = 3
    insight = payload["sections"][0]["blocks"][3]
    insight["content"] = "Overdue rate is 8.13%, and fulfilment improved."
    insight["segments"] = [
        {"type": "text", "text": "Overdue rate is "},
        {
            "type": "claim",
            "segment_id": "segment-overdue",
            "claim_id": "claim-overdue",
            "text": "8.13%",
        },
        {"type": "text", "text": ", and "},
        {
            "type": "claim",
            "segment_id": "segment-fulfilment",
            "claim_id": "claim-fulfilment",
            "text": "fulfilment improved",
        },
        {"type": "text", "text": "."},
    ]
    insight["claims"] = [
        {
            "claim_id": "claim-overdue",
            "claim_type": "metric",
            "text": "Overdue rate is 8.13%",
            "normalized": {"metric": "overdue_rate", "value": 0.0813},
            "verification": {
                "claim_id": "claim-overdue",
                "status": "verified",
                "verification_method": "exact_match",
                "support_score": 1.0,
                "evidence_artifact_ids": ["sql-sales"],
                "verifier_name": "exact-value",
                "verifier_version": "1",
            },
        },
        {
            "claim_id": "claim-fulfilment",
            "claim_type": "trend",
            "text": "Fulfilment improved",
        },
    ]
    insight["evidence"] = [
        {
            "artifact_id": "sql-sales",
            "role": "calculation",
            "selector": {"column": "gmv", "row": 0},
            "target_selector": {
                "kind": "text_segment",
                "block_id": "insight-delta",
                "segment_id": "segment-overdue",
                "claim_id": "claim-overdue",
                "quote": "8.13%",
            },
        },
        {
            "artifact_id": "python-delta",
            "role": "support",
            "selector": {"kind": "result"},
            "target_selector": {
                "kind": "text_segment",
                "block_id": "insight-delta",
                "segment_id": "segment-fulfilment",
                "claim_id": "claim-fulfilment",
                "quote": "fulfilment improved",
            },
        },
    ]
    return payload


def test_schema_v3_round_trip_preserves_claim_segments_and_verification() -> None:
    document = ReportDocument.model_validate(_valid_v3_report_payload())
    insight = document.sections[0].blocks[3]
    restored = ReportDocument.model_validate_json(document.model_dump_json())

    assert document.schema_version == 3
    assert isinstance(insight, InsightBlock)
    assert isinstance(insight.segments[0], TextSegment)
    assert isinstance(insight.segments[1], ClaimSegment)
    assert isinstance(insight.claims[0], Claim)
    assert isinstance(insight.claims[0].verification, ClaimVerificationResult)
    assert insight.claims[0].claim_id == "claim-overdue"
    assert restored == document


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["sections"][0]["blocks"][3].update(
                {"segments": []}
            ),
            "requires segments",
        ),
        (
            lambda payload: payload["sections"][0]["blocks"][3].update({"claims": []}),
            "requires claims",
        ),
        (
            lambda payload: payload["sections"][0]["blocks"][3]["segments"][1].update(
                {"claim_id": "missing"}
            ),
            "unknown claim",
        ),
        (
            lambda payload: payload["sections"][0]["blocks"][3].update(
                {"content": "changed"}
            ),
            "segments must join",
        ),
    ],
)
def test_schema_v3_rejects_invalid_claim_segment_integrity(mutate, message) -> None:
    payload = _valid_v3_report_payload()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        ReportDocument.model_validate(payload)


def test_evidence_packet_preserves_bounded_handle_and_is_public() -> None:
    import dbgpt.core as core

    packet = EvidencePacket(
        handles=[
            EvidenceHandle(
                handle_id="E3",
                artifact_id="sql-overdue",
                selector={
                    "kind": "sql_cell",
                    "row_key": {"month": "2026-08"},
                    "column": "overdue_rate",
                },
                value=0.0813,
            )
        ]
    )

    assert packet.handles[0].handle_id == "E3"
    assert packet.handles[0].selector["column"] == "overdue_rate"
    assert core.EvidencePacket is EvidencePacket


def test_evidence_packet_rejects_duplicate_or_unlocatable_handles() -> None:
    with pytest.raises(ValidationError, match="duplicate evidence handle id"):
        EvidencePacket(
            handles=[
                EvidenceHandle(
                    handle_id="E1",
                    artifact_id="sql-sales",
                    selector={"kind": "result"},
                ),
                EvidenceHandle(
                    handle_id="E1",
                    artifact_id="sql-delta",
                    selector={"kind": "result"},
                ),
            ]
        )

    with pytest.raises(ValidationError, match="selector must not be empty"):
        EvidenceHandle(handle_id="E2", artifact_id="sql-sales", selector={})


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


def test_schema_v1_serialization_omits_v2_execution_fields() -> None:
    report = ReportDocument.model_validate(_valid_report_payload())
    serialized = report.model_dump(mode="json")

    assert serialized["schema_version"] == 1
    for artifact in serialized["artifacts"]:
        assert "status" not in artifact
        assert "duration_ms" not in artifact
        assert "error" not in artifact
        assert "stderr" not in artifact


def test_schema_v1_rejects_v2_execution_fields() -> None:
    payload = _valid_report_payload()
    payload["artifacts"][0]["status"] = "succeeded"
    payload["artifacts"][0]["duration_ms"] = 1

    with pytest.raises(ValidationError, match="schema version 2"):
        ReportDocument.model_validate(payload)


def test_schema_v1_reads_payloads_valid_under_the_original_contract() -> None:
    payload = _valid_report_payload()
    payload["report_id"] = "r" * 65
    payload["artifacts"][0]["execution"]["conv_uid"] = "conv-2"

    report = ReportDocument.model_validate(payload)

    assert report.report_id == "r" * 65
    assert report.artifacts[0].execution.conv_uid == "conv-2"


def test_schema_v2_preserves_failed_execution_without_a_result() -> None:
    report = ReportDocument.model_validate(
        {
            "schema_version": 2,
            "report_id": "report-failed",
            "title": "Failed query",
            "conv_uid": "conv-1",
            "created_at": "2026-08-23T09:30:00",
            "sections": [],
            "artifacts": [
                {
                    "type": "sql",
                    "artifact_id": "sql-failed",
                    "execution": {
                        "conv_uid": "conv-1",
                        "step_id": "step-1",
                        "run_id": "run-1",
                    },
                    "created_at": "2026-08-23T09:30:00",
                    "status": "failed",
                    "duration_ms": 15,
                    "error": "database unavailable",
                    "sql": "SELECT 1",
                    "result": None,
                }
            ],
        }
    )
    restored = ReportDocument.model_validate_json(report.model_dump_json())

    assert restored.schema_version == 2
    assert restored.artifacts[0].status == "failed"
    assert isinstance(restored.artifacts[0], SQLArtifact)
    assert restored.artifacts[0].result is None


def test_schema_v2_preserves_failed_chart_without_fabricated_output() -> None:
    artifact = ChartArtifact(
        artifact_id="chart-failed",
        execution=EXECUTION,
        created_at=CREATED_AT,
        status="failed",
        duration_ms=3,
        error="renderer unavailable",
        chart_type="line",
        output=None,
    )

    restored = ChartArtifact.model_validate_json(artifact.model_dump_json())

    assert restored.status == "failed"
    assert restored.output is None


def test_schema_v2_requires_status_and_duration_for_every_artifact() -> None:
    payload = _valid_report_payload()
    payload["schema_version"] = 2

    with pytest.raises(ValidationError, match="status and duration"):
        ReportDocument.model_validate(payload)


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


def test_report_rejects_artifact_from_another_conversation() -> None:
    payload = _valid_v2_report_payload()
    payload["artifacts"][0]["execution"]["conv_uid"] = "conv-2"

    with pytest.raises(ValidationError, match="artifact conversation conv-2"):
        ReportDocument.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("report_id", "r" * 65, "report_id exceeds 64"),
        ("title", "t" * 513, "title exceeds 512"),
        ("conv_uid", "c" * 256, "conv_uid exceeds 255"),
    ],
)
def test_report_rejects_identifiers_larger_than_persistence_contract(
    field: str, value: str, message: str
) -> None:
    payload = _valid_v2_report_payload()
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        ReportDocument.model_validate(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["sections"][0].update({"section_id": "s" * 129}),
            "section_id exceeds 128",
        ),
        (
            lambda payload: payload["sections"][0]["blocks"][0].update(
                {"block_id": "b" * 129}
            ),
            "block_id exceeds 128",
        ),
        (
            lambda payload: payload["artifacts"][0].update({"artifact_id": "a" * 129}),
            "artifact_id exceeds 128",
        ),
        (
            lambda payload: payload["artifacts"][0]["execution"].update(
                {"conv_uid": "c" * 256}
            ),
            "execution.conv_uid exceeds 255",
        ),
        (
            lambda payload: payload["artifacts"][0]["execution"].update(
                {"step_id": "s" * 256}
            ),
            "execution.step_id exceeds 255",
        ),
        (
            lambda payload: payload["artifacts"][0]["execution"].update(
                {"message_id": "m" * 256}
            ),
            "execution.message_id exceeds 255",
        ),
        (
            lambda payload: payload["artifacts"][0]["execution"].update(
                {"task_id": "t" * 65}
            ),
            "execution.task_id exceeds 64",
        ),
        (
            lambda payload: payload["artifacts"][0]["execution"].update(
                {"run_id": "r" * 65}
            ),
            "execution.run_id exceeds 64",
        ),
    ],
)
def test_schema_v2_rejects_nested_ids_larger_than_persistence_contract(
    mutate, message: str
) -> None:
    payload = _valid_v2_report_payload()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
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
        "Claim",
        "ClaimSegment",
        "ClaimType",
        "ClaimVerificationResult",
        "EvidenceHandle",
        "EvidencePacket",
        "EvidenceReference",
        "ExecutionReference",
        "InsightBlock",
        "MetricBlock",
        "PythonArtifact",
        "ReferenceArtifact",
        "ReportBlock",
        "ReportDocument",
        "ReportSection",
        "ReportTextSegment",
        "SQLArtifact",
        "TableBlock",
        "TextBlock",
        "TextSegment",
        "VerificationMethod",
        "VerificationStatus",
    }

    assert all(hasattr(core, name) for name in public_names)
    assert core.ReportDocument is ReportDocument
    assert core.SQLArtifact is SQLArtifact
    assert core.Claim is Claim
    assert core.ClaimSegment is ClaimSegment
    assert core.EvidenceHandle is EvidenceHandle
