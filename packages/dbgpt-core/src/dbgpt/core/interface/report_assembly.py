"""Deterministic assembly of executed artifacts into report blocks."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .report import (
    Artifact,
    ChartArtifact,
    ChartBlock,
    EvidenceReference,
    InsightBlock,
    MetricBlock,
    PythonArtifact,
    ReferenceArtifact,
    ReportBlock,
    ReportDocument,
    ReportSection,
    SQLArtifact,
    TableBlock,
)


class ReportAssembler:
    """Map captured artifacts to native blocks without inventing results."""

    def assemble(
        self,
        *,
        report_id: str,
        title: str,
        conv_uid: str,
        final_content: str,
        artifacts: Sequence[Artifact],
        supporting_artifact_ids: Iterable[str] = (),
        created_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReportDocument:
        """Build a schema-v2 report from one completed execution snapshot."""
        content = final_content.strip()
        if not content:
            raise ValueError("final_content must not be blank")

        owned_artifacts = [artifact.model_copy(deep=True) for artifact in artifacts]
        artifact_by_id = {
            artifact.artifact_id: artifact for artifact in owned_artifacts
        }
        support_ids = [
            str(item) for item in supporting_artifact_ids if str(item).strip()
        ]
        summary_evidence = self._summary_evidence(artifact_by_id, support_ids)
        sections: List[ReportSection] = []
        # A final answer is not, by itself, report evidence. Keep it as task
        # metadata unless its caller supplied explicit, validated support.
        if summary_evidence:
            sections.append(
                ReportSection(
                    section_id="summary",
                    title="核心结论",
                    blocks=[
                        InsightBlock(
                            block_id="summary-insight",
                            title="核心结论",
                            content=content,
                            evidence=summary_evidence,
                        )
                    ],
                )
            )
        detail_blocks = self._detail_blocks(owned_artifacts, artifact_by_id)
        if detail_blocks:
            sections.append(
                ReportSection(
                    section_id="analysis",
                    title="分析结果",
                    blocks=detail_blocks,
                )
            )

        document_metadata = dict(metadata or {})
        document_metadata["task_summary"] = content
        document_metadata["assembly"] = {
            "version": 1,
            "artifact_count": len(owned_artifacts),
            "block_count": sum(len(section.blocks) for section in sections),
        }
        return ReportDocument(
            schema_version=2,
            report_id=report_id,
            title=title,
            conv_uid=conv_uid,
            created_at=created_at or datetime.now(timezone.utc),
            sections=sections,
            artifacts=owned_artifacts,
            metadata=document_metadata,
        )

    def _summary_evidence(
        self,
        artifact_by_id: Dict[str, Artifact],
        supporting_artifact_ids: Sequence[str],
    ) -> List[EvidenceReference]:
        evidence = []
        seen = set()
        for artifact_id in supporting_artifact_ids:
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            artifact = artifact_by_id.get(artifact_id)
            if artifact is None:
                raise ValueError(f"unknown supporting artifact {artifact_id}")
            if artifact.status != "succeeded":
                raise ValueError(
                    f"supporting artifact must have succeeded: {artifact_id}"
                )
            if isinstance(artifact, ReferenceArtifact):
                role = "source"
                selector = dict(artifact.locator) or {"kind": "reference"}
            elif isinstance(artifact, (SQLArtifact, PythonArtifact)):
                role = "calculation"
                selector = {"kind": "result"}
            elif isinstance(artifact, ChartArtifact):
                role = "support"
                selector = {"kind": "chart"}
            else:
                continue
            evidence.append(
                EvidenceReference(
                    artifact_id=artifact.artifact_id,
                    role=role,
                    selector=selector,
                )
            )
        return evidence

    def _detail_blocks(
        self,
        artifacts: Sequence[Artifact],
        artifact_by_id: Dict[str, Artifact],
    ) -> List[ReportBlock]:
        blocks: List[ReportBlock] = []
        for artifact in artifacts:
            if artifact.status != "succeeded":
                continue
            if isinstance(artifact, (SQLArtifact, PythonArtifact)):
                tabular = self._tabular_result(artifact)
                if tabular is None:
                    continue
                columns, rows = tabular
                scalar = self._scalar_result(columns, rows)
                if scalar is not None:
                    column, value = scalar
                    blocks.append(
                        MetricBlock(
                            block_id=self._block_id("metric", artifact.artifact_id),
                            title=artifact.name or column,
                            value=value,
                            evidence=[
                                EvidenceReference(
                                    artifact_id=artifact.artifact_id,
                                    role="calculation",
                                    selector={
                                        "kind": "cell",
                                        "row": 0,
                                        "column": column,
                                    },
                                    target_selector={"field": "value"},
                                )
                            ],
                        )
                    )
                else:
                    blocks.append(
                        TableBlock(
                            block_id=self._block_id("table", artifact.artifact_id),
                            title=artifact.name or "Result table",
                            data_artifact_id=artifact.artifact_id,
                            evidence=[
                                EvidenceReference(
                                    artifact_id=artifact.artifact_id,
                                    role="calculation",
                                    selector={"kind": "result"},
                                )
                            ],
                        )
                    )
            elif isinstance(artifact, ChartArtifact):
                blocks.append(
                    ChartBlock(
                        block_id=self._block_id("chart", artifact.artifact_id),
                        title=artifact.name or artifact.chart_type,
                        chart_artifact_id=artifact.artifact_id,
                        evidence=self._chart_evidence(artifact, artifact_by_id),
                    )
                )
        return blocks

    @staticmethod
    def _tabular_result(
        artifact: SQLArtifact | PythonArtifact,
    ) -> Optional[Tuple[List[str], List[Any]]]:
        result = artifact.result
        inline = result.inline if result is not None else None
        if not isinstance(inline, dict):
            return None
        columns = inline.get("columns")
        rows = inline.get("rows")
        if not isinstance(columns, list) or not all(
            isinstance(column, str) for column in columns
        ):
            return None
        if not isinstance(rows, list):
            return None
        return columns, rows

    @staticmethod
    def _scalar_result(
        columns: List[str], rows: List[Any]
    ) -> Optional[Tuple[str, Any]]:
        if len(columns) != 1 or len(rows) != 1:
            return None
        row = rows[0]
        if isinstance(row, list) and len(row) == 1:
            return columns[0], row[0]
        if isinstance(row, dict) and set(row) == {columns[0]}:
            return columns[0], row[columns[0]]
        return None

    def _chart_evidence(
        self,
        chart: ChartArtifact,
        artifact_by_id: Dict[str, Artifact],
    ) -> List[EvidenceReference]:
        evidence = [
            EvidenceReference(
                artifact_id=chart.artifact_id,
                role="calculation",
                selector={"kind": "chart"},
            )
        ]
        visited = {chart.artifact_id}

        def append_inputs(artifact: Artifact) -> None:
            for input_id in artifact.input_artifact_ids:
                if input_id in visited:
                    continue
                visited.add(input_id)
                dependency = artifact_by_id.get(input_id)
                if dependency is None:
                    continue
                is_reference = isinstance(dependency, ReferenceArtifact)
                role = "source" if is_reference else "calculation"
                selector = (
                    dict(dependency.locator) or {"kind": "reference"}
                    if is_reference
                    else {"kind": "result"}
                )
                evidence.append(
                    EvidenceReference(
                        artifact_id=input_id,
                        role=role,
                        selector=selector,
                    )
                )
                append_inputs(dependency)

        append_inputs(chart)
        return evidence

    @staticmethod
    def _block_id(prefix: str, artifact_id: str) -> str:
        token = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()[:20]
        return f"{prefix}-{token}"


__all__ = ["ReportAssembler"]
