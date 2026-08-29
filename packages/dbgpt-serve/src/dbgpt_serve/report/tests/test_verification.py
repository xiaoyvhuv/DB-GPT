"""Tests for deterministic report claim verification."""

from datetime import datetime, timezone

from dbgpt.core.interface.evidence_packet import EvidencePacket
from dbgpt.core.interface.report import (
    ArtifactContent,
    Claim,
    ExecutionReference,
    SQLArtifact,
)
from dbgpt_serve.report.verification.evidence import (
    EvidencePacketBuilder,
    EvidenceResolver,
)


def _sql_artifact() -> SQLArtifact:
    return SQLArtifact(
        artifact_id="sql-monthly-risk",
        execution=ExecutionReference(conv_uid="conv-1", step_id="step-1"),
        created_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        status="succeeded",
        duration_ms=3,
        sql="SELECT month, overdue_rate FROM monthly_risk",
        columns=["month", "overdue_rate"],
        row_count=1,
        result=ArtifactContent(
            inline={
                "columns": ["month", "overdue_rate"],
                "rows": [{"month": "2026-08", "overdue_rate": 0.0813}],
            }
        ),
    )


def test_packet_builder_exposes_sql_cells_with_metric_metadata() -> None:
    artifact = _sql_artifact()

    packet = EvidencePacketBuilder().build([artifact])

    handle = next(
        item for item in packet.handles if item.selector["column"] == "overdue_rate"
    )
    assert handle.artifact_id == artifact.artifact_id
    assert handle.selector == {"kind": "sql_cell", "row": 0, "column": "overdue_rate"}
    assert handle.metadata["metric_key"] == "overdue_rate"


def test_resolver_rejects_claim_handle_not_in_current_packet() -> None:
    artifact = _sql_artifact()
    claim = Claim(
        claim_id="claim-rate",
        claim_type="metric",
        text="8 月逾期率为 8.13%",
        normalized={"evidence_handles": ["forged-handle"]},
    )

    resolved = EvidenceResolver().resolve(claim, EvidencePacket(), [artifact])

    assert resolved == []
