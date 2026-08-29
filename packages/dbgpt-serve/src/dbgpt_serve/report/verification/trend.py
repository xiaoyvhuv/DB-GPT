"""Deterministic trend verification based on registered metric semantics."""

from dbgpt.core.interface.metric import BetterDirection, MetricDefinition
from dbgpt.core.interface.report import Claim, ClaimVerificationResult


class TrendRuleVerifier:
    """Verify directional and significant trends without inventing thresholds."""

    def verify(
        self,
        claim: Claim,
        current: float,
        baseline: float,
        definition: MetricDefinition,
    ) -> ClaimVerificationResult:
        magnitude = claim.normalized.get("magnitude")
        if magnitude == "significant" and definition.significant_change is None:
            return self._result(
                claim, "insufficient_evidence", "INSUFFICIENT_RULE", current, baseline
            )
        delta = current - baseline
        improving = (
            definition.better_direction == BetterDirection.LOWER and delta < 0
        ) or (definition.better_direction == BetterDirection.HIGHER and delta > 0)
        expected = claim.normalized.get("direction")
        if expected == "improvement" and not improving:
            return self._result(
                claim, "contradicted", "DIRECTION_MISMATCH", current, baseline
            )
        if magnitude == "significant" and abs(delta) < definition.significant_change:
            return self._result(
                claim,
                "contradicted",
                "SIGNIFICANCE_THRESHOLD_NOT_MET",
                current,
                baseline,
            )
        return self._result(claim, "verified", None, current, baseline)

    @staticmethod
    def _result(claim, status, reason_code, current, baseline):
        return ClaimVerificationResult(
            claim_id=claim.claim_id,
            status=status,
            verification_method="trend_rule",
            evidence_artifact_ids=[],
            verifier_name="trend-rule",
            verifier_version="1",
            reason_code=reason_code,
            details={
                "current": current,
                "previous": baseline,
                "delta": current - baseline,
            },
        )
