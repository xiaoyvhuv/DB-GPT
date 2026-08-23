"""Report service behavior tests."""

from unittest.mock import MagicMock

import pytest

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
