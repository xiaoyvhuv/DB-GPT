"""Metric semantics used by deterministic report claim verification."""

from enum import Enum
from typing import List, Optional

from dbgpt._private.pydantic import BaseModel, ConfigDict, Field, field_validator


class _MetricModel(BaseModel):
    """Strict base for stable metric semantic values."""

    model_config = ConfigDict(extra="forbid")


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


class BetterDirection(str, Enum):
    """Whether a greater value indicates a better business outcome."""

    HIGHER = "higher"
    LOWER = "lower"
    NEUTRAL = "neutral"


class MetricDefinition(_MetricModel):
    """Stable semantic rules for matching and interpreting one metric."""

    metric_key: str
    display_name: str
    unit: str
    aliases: List[str] = Field(default_factory=list)
    better_direction: BetterDirection = BetterDirection.NEUTRAL
    absolute_tolerance: Optional[float] = Field(default=None, ge=0)
    relative_tolerance: Optional[float] = Field(default=None, ge=0)
    significant_change: Optional[float] = Field(default=None, ge=0)

    _validate_metric_key = field_validator("metric_key")(_require_non_blank)
    _validate_display_name = field_validator("display_name")(_require_non_blank)
    _validate_unit = field_validator("unit")(_require_non_blank)

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, aliases: List[str]) -> List[str]:
        return [_require_non_blank(alias) for alias in aliases]


__all__ = ["BetterDirection", "MetricDefinition"]
