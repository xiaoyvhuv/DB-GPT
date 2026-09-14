"""Application service for owner-scoped report persistence."""

from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from dbgpt.core.interface.report import (
    Artifact,
    ReportDocument,
    ReportPresentationBinding,
)
from dbgpt.core.interface.report_assembly import ReportAssembler

from .domain import ReportEvidenceDetail, ReportRunRecord, ReportRunSummary
from .models.dao import ReportDao


class ReportNotFoundError(LookupError):
    """The requested report run is not visible in the owner scope."""


class ReportBlockNotFoundError(LookupError):
    """The requested block is absent from an otherwise visible report run."""


class ReportService:
    """Thin orchestration layer above the transactional report DAO."""

    def __init__(
        self,
        dao: Optional[ReportDao] = None,
        assembler: Optional[ReportAssembler] = None,
        verification_pipeline: Optional[
            Callable[[ReportDocument], ReportDocument]
        ] = None,
    ) -> None:
        self._dao = dao or ReportDao()
        self._assembler = assembler or ReportAssembler()
        self._verification_pipeline = verification_pipeline

    def save(
        self,
        *,
        owner_id: str,
        document: ReportDocument,
        trigger_type: str = "interactive",
        parent_run_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> ReportRunRecord:
        """Persist one immutable report revision."""
        if self._verification_pipeline is not None and document.schema_version == 3:
            document = self._verification_pipeline(document)
        kwargs = {
            "owner_id": owner_id,
            "trigger_type": trigger_type,
            "parent_run_id": parent_run_id,
        }
        if run_id is not None:
            kwargs["run_id"] = run_id
        return self._dao.save(document, **kwargs)

    def assemble_and_save(
        self,
        *,
        owner_id: str,
        report_id: str,
        title: str,
        conv_uid: str,
        final_content: str,
        artifacts: Sequence[Artifact],
        supporting_artifact_ids: Iterable[str] = (),
        presentation_bindings: Sequence[
            ReportPresentationBinding | Dict[str, Any]
        ] = (),
        presentation: Optional[Dict[str, Any]] = None,
        include_chart_blocks: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[datetime] = None,
        trigger_type: str = "interactive",
        parent_run_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> ReportRunRecord:
        """Assemble one captured turn and persist it as an immutable revision."""
        document = self._assembler.assemble(
            report_id=report_id,
            title=title,
            conv_uid=conv_uid,
            final_content=final_content,
            artifacts=artifacts,
            supporting_artifact_ids=supporting_artifact_ids,
            presentation_bindings=presentation_bindings,
            presentation=presentation,
            include_chart_blocks=include_chart_blocks,
            created_at=created_at,
            metadata=metadata,
        )
        return self.save(
            owner_id=owner_id,
            document=document,
            trigger_type=trigger_type,
            parent_run_id=parent_run_id,
            run_id=run_id,
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
        *,
        limit: int = 50,
    ) -> List[ReportEvidenceDetail]:
        """Return evidence while distinguishing missing and evidence-free blocks."""
        run = self.get_run(owner_id, report_id, run_id)
        if not any(
            block.block_id == block_id
            for section in run.document.sections
            for block in section.blocks
        ):
            raise ReportBlockNotFoundError(block_id)
        return self._dao.get_block_evidence(
            owner_id, report_id, run_id, block_id, limit=limit
        )
