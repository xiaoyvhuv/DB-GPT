"""Bounded evidence handles for claim-aware report generation.

This module defines a cross-layer value object only. Building handles from
artifacts and resolving them back to report evidence belong to higher-level
application services.
"""

from typing import Any, Dict, List, Optional

from dbgpt._private.pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class _EvidencePacketModel(BaseModel):
    """Strict base for evidence packet wire values."""

    model_config = ConfigDict(extra="forbid")


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class EvidenceHandle(_EvidencePacketModel):
    """One bounded reference to a selector within a captured artifact."""

    handle_id: str
    artifact_id: str
    selector: Dict[str, Any]
    label: Optional[str] = None
    value: Optional[Any] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _validate_handle_id = field_validator("handle_id")(_require_non_blank)
    _validate_artifact_id = field_validator("artifact_id")(_require_non_blank)
    _validate_label = field_validator("label")(_require_non_blank)

    @model_validator(mode="after")
    def validate_selector(self) -> "EvidenceHandle":
        if not self.selector:
            raise ValueError("selector must not be empty")
        return self


class EvidencePacket(_EvidencePacketModel):
    """A deterministic collection of evidence handles for one report attempt."""

    handles: List[EvidenceHandle] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_handle_ids(self) -> "EvidencePacket":
        seen = set()
        for handle in self.handles:
            if handle.handle_id in seen:
                raise ValueError(f"duplicate evidence handle id: {handle.handle_id}")
            seen.add(handle.handle_id)
        return self


__all__ = ["EvidenceHandle", "EvidencePacket"]
