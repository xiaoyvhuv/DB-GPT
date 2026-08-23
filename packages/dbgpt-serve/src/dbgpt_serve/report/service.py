"""Application service for owner-scoped report persistence."""

from typing import List, Optional

from dbgpt.core.interface.report import ReportDocument

from .domain import ReportEvidenceDetail, ReportRunRecord, ReportRunSummary
from .models.dao import ReportDao


class ReportNotFoundError(LookupError):
    """The requested report run is not visible in the owner scope."""


class ReportBlockNotFoundError(LookupError):
    """The requested block is absent from an otherwise visible report run."""


class ReportService:
    """Thin orchestration layer above the transactional report DAO."""

    def __init__(self, dao: Optional[ReportDao] = None) -> None:
        self._dao = dao or ReportDao()

    def save(
        self,
        *,
        owner_id: str,
        document: ReportDocument,
        trigger_type: str = "interactive",
        parent_run_id: Optional[str] = None,
    ) -> ReportRunRecord:
        """Persist one immutable report revision."""
        return self._dao.save(
            document,
            owner_id=owner_id,
            trigger_type=trigger_type,
            parent_run_id=parent_run_id,
        )

    def get_latest(self, owner_id: str, report_id: str) -> ReportRunRecord:
        """Return the latest visible run or raise a domain not-found error."""
        record = self._dao.get_latest(owner_id, report_id)
        if record is None:
            raise ReportNotFoundError(report_id)
        return record

    def get_run(self, owner_id: str, report_id: str, run_id: str) -> ReportRunRecord:
        """Return one exact visible run or raise a domain not-found error."""
        record = self._dao.get_run(owner_id, report_id, run_id)
        if record is None:
            raise ReportNotFoundError(run_id)
        return record

    def list_runs(
        self,
        owner_id: str,
        report_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ReportRunSummary]:
        """List revisions only after verifying the report is owner-visible."""
        self.get_latest(owner_id, report_id)
        return self._dao.list_runs(owner_id, report_id, limit=limit, offset=offset)

    def get_block_evidence(
        self,
        owner_id: str,
        report_id: str,
        run_id: str,
        block_id: str,
    ) -> List[ReportEvidenceDetail]:
        """Return evidence while distinguishing missing and evidence-free blocks."""
        run = self.get_run(owner_id, report_id, run_id)
        if not any(
            block.block_id == block_id
            for section in run.document.sections
            for block in section.blocks
        ):
            raise ReportBlockNotFoundError(block_id)
        return self._dao.get_block_evidence(owner_id, report_id, run_id, block_id)
