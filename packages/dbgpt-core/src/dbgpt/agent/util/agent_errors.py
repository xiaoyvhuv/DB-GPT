"""Small, dependency-free error classification helpers for enhanced agents."""

from __future__ import annotations

from enum import Enum
from typing import Optional, Union


class ErrorCategory(str, Enum):
    """Stable categories exposed in enhanced ReAct fail reasons."""

    RETRYABLE = "retryable"
    TOOL_EXECUTION = "tool_execution"
    MODEL = "model"
    CONTEXT = "context"
    VALIDATION = "validation"


def classify_agent_error(
    error: Union[BaseException, str, None],
    *,
    default: ErrorCategory = ErrorCategory.RETRYABLE,
) -> ErrorCategory:
    """Classify common agent failures without changing legacy retry behavior."""
    text = str(error or "").lower()
    if any(token in text for token in ("context length", "token limit", "too many tokens")):
        return ErrorCategory.CONTEXT
    if any(token in text for token in ("validation", "invalid", "parse error")):
        return ErrorCategory.VALIDATION
    if any(token in text for token in ("model", "llm", "rate limit", "timeout")):
        return ErrorCategory.MODEL
    if any(token in text for token in ("tool", "function", "sql", "python", "execution")):
        return ErrorCategory.TOOL_EXECUTION
    return default


def format_fail_reason(category: ErrorCategory, reason: Optional[str]) -> str:
    """Keep the original reason while adding a machine-readable category."""
    detail = str(reason or "Unknown agent failure").strip()
    return f"[{category.value}] {detail}"


__all__ = ["ErrorCategory", "classify_agent_error", "format_fail_reason"]
