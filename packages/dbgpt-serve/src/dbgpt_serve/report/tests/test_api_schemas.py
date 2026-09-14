"""Wire-schema tests for report persistence APIs."""

from datetime import datetime, timezone

from dbgpt_serve.report.api.schemas import (
    ReportEvidenceResponse,
    ReportRunResponse,
    ReportRunSummaryResponse,
    SaveReportRequest,
)
from dbgpt_serve.report.domain import ReportEvidenceDetail, ReportRunRecord

from .test_dao import _document


def test_save_request_parses_the_strict_protocol_document():
    request = SaveReportRequest(document=_document().model_dump())

    assert request.document.report_id == "report-1"
    assert request.trigger_type == "interactive"


def test_run_responses_map_immutable_domain_values():
    record = ReportRunRecord(
        run_id="run-1",
        report_id="report-1",
        revision=1,
        schema_version=1,
        trigger_type="interactive",
        parent_run_id=None,
        status="completed",
        created_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        document=_document(),
    )

    summary = ReportRunSummaryResponse.from_domain(record)
    response = ReportRunResponse.from_record(record)

    assert summary.model_dump()["run_id"] == "run-1"
    assert response.document == record.document


def test_evidence_response_preserves_typed_artifact():
    document = _document()
    detail = ReportEvidenceDetail(
        section_id="summary",
        block_id="metric-gmv",
        ordinal=0,
        evidence=document.sections[0].blocks[0].evidence[0],
        artifact=document.artifacts[0],
    )

    response = ReportEvidenceResponse.from_domain(detail)

    assert response.artifact.type == "sql"
    assert response.evidence.selector == {"row": 0, "column": "gmv"}
