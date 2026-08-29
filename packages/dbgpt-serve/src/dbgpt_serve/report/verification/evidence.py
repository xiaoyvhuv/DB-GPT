"""Build and resolve bounded evidence handles from captured artifacts."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Dict, Iterable, List, Sequence

from dbgpt.core.interface.evidence_packet import EvidenceHandle, EvidencePacket
from dbgpt.core.interface.report import Artifact, Claim, PythonArtifact, SQLArtifact


@dataclass(frozen=True)
class ResolvedEvidence:
    """One handle resolved against an artifact in the current report snapshot."""

    artifact: Artifact
    selector: Dict[str, Any]
    value: Any
    metadata: Dict[str, Any]


class EvidencePacketBuilder:
    """Expose bounded cell handles from successful inline tabular artifacts."""

    def build(self, artifacts: Iterable[Artifact]) -> EvidencePacket:
        handles: List[EvidenceHandle] = []
        for artifact in artifacts:
            if not isinstance(artifact, (SQLArtifact, PythonArtifact)):
                continue
            if artifact.status != "succeeded":
                continue
            table = _tabular_result(artifact)
            if table is None:
                continue
            columns, rows = table
            for row_index, row in enumerate(rows):
                for column in columns:
                    value = _cell_value(row, column, columns)
                    selector = {"kind": "sql_cell", "row": row_index, "column": column}
                    handles.append(
                        EvidenceHandle(
                            handle_id=_handle_id(
                                artifact.artifact_id, row_index, column
                            ),
                            artifact_id=artifact.artifact_id,
                            selector=selector,
                            value=value,
                            metadata={"metric_key": column},
                        )
                    )
        return EvidencePacket(handles=handles)


class EvidenceResolver:
    """Resolve only explicitly requested handles from the current packet."""

    def resolve(
        self,
        claim: Claim,
        packet: EvidencePacket,
        artifacts: Sequence[Artifact],
    ) -> List[ResolvedEvidence]:
        handles = {handle.handle_id: handle for handle in packet.handles}
        artifact_by_id = {artifact.artifact_id: artifact for artifact in artifacts}
        requested = claim.normalized.get("evidence_handles", [])
        if not isinstance(requested, list):
            return []
        return [
            ResolvedEvidence(
                artifact=artifact_by_id[handle.artifact_id],
                selector=dict(handle.selector),
                value=handle.value,
                metadata=dict(handle.metadata),
            )
            for handle_id in requested
            if isinstance(handle_id, str)
            and (handle := handles.get(handle_id)) is not None
            and handle.artifact_id in artifact_by_id
        ]


def _tabular_result(
    artifact: SQLArtifact | PythonArtifact,
) -> tuple[List[str], List[Any]] | None:
    result = artifact.result
    inline = result.inline if result is not None else None
    if not isinstance(inline, dict):
        return None
    columns = inline.get("columns")
    rows = inline.get("rows")
    if not isinstance(columns, list) or not all(
        isinstance(item, str) for item in columns
    ):
        return None
    if not isinstance(rows, list):
        return None
    return columns, rows


def _cell_value(row: Any, column: str, columns: List[str]) -> Any:
    if isinstance(row, dict):
        return row.get(column)
    if isinstance(row, list):
        return row[columns.index(column)] if columns.index(column) < len(row) else None
    return None


def _handle_id(artifact_id: str, row_index: int, column: str) -> str:
    value = f"{artifact_id}:{row_index}:{column}".encode("utf-8")
    return f"e-{sha256(value).hexdigest()[:20]}"


__all__ = ["EvidencePacketBuilder", "EvidenceResolver", "ResolvedEvidence"]
