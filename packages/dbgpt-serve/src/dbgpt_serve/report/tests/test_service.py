"""Report service behavior tests."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from dbgpt.core.interface.report import (
    ArtifactContent,
    ExecutionReference,
    SQLArtifact,
)

from ..domain import ReportRunRecord
from ..service import ReportBlockNotFoundError, ReportNotFoundError, ReportService
from .test_dao import _document


def test_save_delegates_server_owned_owner_and_run_metadata():
    dao = MagicMock()
    expected = MagicMock(spec=ReportRunRecord)
    dao.save.return_value = expected
    service = ReportService(dao)
    document = _document()

    actual = service.save(
        owner_id="alice",
        document=document,
        trigger_type="replay",
        parent_run_id="run-old",
    )

    assert actual is expected
    dao.save.assert_called_once_with(
        document,
        owner_id="alice",
        trigger_type="replay",
        parent_run_id="run-old",
    )


def test_save_runs_optional_pipeline_only_for_schema_v3_documents():
    dao = MagicMock()
    schema_v3 = MagicMock(schema_version=3)
    verified = MagicMock(schema_version=3)
    pipeline = MagicMock(return_value=verified)

    ReportService(dao, verification_pipeline=pipeline).save(
        owner_id="alice", document=schema_v3
    )

    pipeline.assert_called_once_with(schema_v3)
    assert dao.save.call_args.args[0] is verified


def test_save_skips_pipeline_for_legacy_document():
    dao = MagicMock()
    pipeline = MagicMock()
    document = _document()

    ReportService(dao, verification_pipeline=pipeline).save(
        owner_id="alice", document=document
    )

    pipeline.assert_not_called()
    assert dao.save.call_args.args[0] is document


def test_assemble_and_save_builds_v2_document_from_captured_artifacts():
    dao = MagicMock()
    expected = MagicMock(spec=ReportRunRecord)
    dao.save.return_value = expected
    artifact = SQLArtifact(
        artifact_id="sql-total",
        execution=ExecutionReference(
            conv_uid="conv-1", step_id="step-1", run_id="turn-1"
        ),
        created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        status="succeeded",
        duration_ms=3,
        sql="SELECT 42 AS total",
        columns=["total"],
        row_count=1,
        result=ArtifactContent(inline={"columns": ["total"], "rows": [[42]]}),
    )

    actual = ReportService(dao).assemble_and_save(
        owner_id="alice",
        report_id="report-conv-1",
        title="Sales",
        conv_uid="conv-1",
        final_content="The total is 42.",
        artifacts=[artifact],
    )

    assert actual is expected
    document = dao.save.call_args.args[0]
    assert document.schema_version == 2
    assert document.report_id == "report-conv-1"
    assert document.artifacts[0].artifact_id == "sql-total"
    assert document.sections[0].blocks[0].value == 42
    assert dao.save.call_args.kwargs == {
        "owner_id": "alice",
        "trigger_type": "interactive",
        "parent_run_id": None,
    }


def test_get_latest_raises_domain_not_found():
    dao = MagicMock()
    dao.get_latest.return_value = None

    with pytest.raises(ReportNotFoundError):
        ReportService(dao).get_latest("alice", "missing")


def test_list_runs_requires_visible_report():
    dao = MagicMock()
    dao.get_latest.return_value = None

    with pytest.raises(ReportNotFoundError):
        ReportService(dao).list_runs("alice", "missing")

    dao.list_runs.assert_not_called()


def test_get_block_evidence_distinguishes_missing_block_from_empty_evidence():
    dao = MagicMock()
    run = MagicMock()
    run.document = _document()
    dao.get_run.return_value = run
    dao.get_block_evidence.return_value = []
    service = ReportService(dao)

    assert service.get_block_evidence("alice", "report-1", "run-1", "chart-gmv") == []
    with pytest.raises(ReportBlockNotFoundError):
        service.get_block_evidence("alice", "report-1", "run-1", "missing")


def test_get_block_evidence_rejects_invisible_run_before_index_query():
    dao = MagicMock()
    dao.get_run.return_value = None
    service = ReportService(dao)

    with pytest.raises(ReportNotFoundError):
        service.get_block_evidence("bob", "report-1", "run-1", "metric-gmv")

    dao.get_block_evidence.assert_not_called()
