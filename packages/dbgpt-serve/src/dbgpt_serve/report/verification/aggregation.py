"""Business importance and transparent report grounding aggregation."""

from dataclasses import dataclass
from typing import Dict, Iterable, Set

from dbgpt.core.interface.report import Claim, ClaimVerificationResult


@dataclass(frozen=True)
class ClaimImportance:
    """Business importance kept separate from factual verification."""

    claim_id: str
    level: str
    weight: int
    reason_code: str


@dataclass(frozen=True)
class ReportGroundingSummary:
    """Explainable report-level grounding statistics."""

    evidence_coverage: float
    factual_grounding_score: float
    business_critical_grounding_score: float
    contradicted_claim_count: int


class RuleBasedWeightResolver:
    """Mark user-primary metrics as critical without altering their truth value."""

    def __init__(self, primary_metric_keys: Set[str] | None = None) -> None:
        self._primary_metric_keys = primary_metric_keys or set()

    def resolve(self, claim: Claim) -> ClaimImportance:
        if claim.normalized.get("metric_key") in self._primary_metric_keys:
            return ClaimImportance(claim.claim_id, "critical", 3, "PRIMARY_METRIC")
        return ClaimImportance(claim.claim_id, "supporting", 1, "DEFAULT_SUPPORTING")


class ReportAggregator:
    """Aggregate verifiable claims without turning results into one opaque score."""

    def aggregate(
        self,
        *,
        claims: Iterable[Claim],
        verifications: Dict[str, ClaimVerificationResult],
        importance: Iterable[ClaimImportance],
        evidence_counts: Dict[str, int],
    ) -> ReportGroundingSummary:
        eligible = [
            claim
            for claim in claims
            if claim.claim_type != "recommendation"
            and claim.normalized.get("verifiable", True) is not False
        ]
        weights = {item.claim_id: item.weight for item in importance}
        supports = [_support(verifications.get(claim.claim_id)) for claim in eligible]
        coverage = (
            sum(evidence_counts.get(claim.claim_id, 0) > 0 for claim in eligible)
            / len(eligible)
            if eligible
            else 0.0
        )
        factual = sum(supports) / len(supports) if supports else 0.0
        total_weight = sum(weights.get(claim.claim_id, 1) for claim in eligible)
        weighted = (
            sum(
                weights.get(claim.claim_id, 1)
                * _support(verifications.get(claim.claim_id))
                for claim in eligible
            )
            / total_weight
            if total_weight
            else 0.0
        )
        contradicted = sum(
            result.status == "contradicted" for result in verifications.values()
        )
        return ReportGroundingSummary(coverage, factual, weighted, contradicted)


def _support(result: ClaimVerificationResult | None) -> float:
    if result is None:
        return 0.0
    if result.status == "verified":
        return 1.0
    if result.status == "partially_supported":
        return result.support_score if result.support_score is not None else 0.5
    return 0.0


__all__ = [
    "ClaimImportance",
    "ReportAggregator",
    "ReportGroundingSummary",
    "RuleBasedWeightResolver",
]
