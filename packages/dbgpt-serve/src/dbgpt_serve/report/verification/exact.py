"""Exact numeric verification for metric claims."""

from typing import Iterable

from dbgpt.core.interface.metric import MetricDefinition
from dbgpt.core.interface.report import Claim, ClaimVerificationResult

from .evidence import ResolvedEvidence


class ExactMetricVerifier:
    """Verify a metric claim only when semantic scope and numeric value match."""

    def verify(
        self,
        claim: Claim,
        evidence: Iterable[ResolvedEvidence],
        definition: MetricDefinition,
    ) -> ClaimVerificationResult:
        expected_metric = claim.normalized.get("metric_key")
        expected_time = claim.normalized.get("time_scope")
        expected_dimensions = claim.normalized.get("dimensions", {})
        expected_value = _normalize(
            claim.normalized.get("value"), claim.normalized.get("unit")
        )
        for item in evidence:
            if item.metadata.get("metric_key") != expected_metric:
                continue
            if (
                expected_time is not None
                and item.metadata.get("time_scope") != expected_time
            ):
                return _result(claim, "unsupported", "TIME_SCOPE_MISMATCH", item)
            if item.metadata.get("dimensions", {}) != expected_dimensions:
                return _result(claim, "unsupported", "DIMENSION_MISMATCH", item)
            actual_value = _normalize(
                item.value, item.metadata.get("unit", definition.unit)
            )
            if expected_value is None or actual_value is None:
                return _result(claim, "unsupported", "VALUE_NOT_NUMERIC", item)
            tolerance = definition.absolute_tolerance or 0.0
            if abs(expected_value - actual_value) <= max(tolerance, 1e-12):
                return _result(
                    claim, "verified", None, item, expected_value, actual_value
                )
            return _result(
                claim,
                "contradicted",
                "VALUE_MISMATCH",
                item,
                expected_value,
                actual_value,
            )
        return _result(claim, "insufficient_evidence", "NO_MATCHING_EVIDENCE")


def _normalize(value, unit):
    if not isinstance(value, (int, float)):
        return None
    return float(value) / 100 if unit in {"%", "percent"} else float(value)


def _result(claim, status, reason_code, item=None, expected=None, actual=None):
    details = {}
    artifact_ids = []
    if item is not None:
        artifact_ids = [item.artifact.artifact_id]
        details["normalized_evidence_value"] = actual
    if expected is not None:
        details["normalized_claim_value"] = expected
    return ClaimVerificationResult(
        claim_id=claim.claim_id,
        status=status,
        verification_method="exact_match",
        evidence_artifact_ids=artifact_ids,
        verifier_name="exact-metric",
        verifier_version="1",
        reason_code=reason_code,
        details=details,
    )
