"""Route claims to supported deterministic verifiers only."""

from typing import Dict, Sequence

from dbgpt.core.interface.evidence_packet import EvidencePacket
from dbgpt.core.interface.metric import MetricDefinition
from dbgpt.core.interface.report import Artifact, Claim, ClaimVerificationResult

from .evidence import EvidenceResolver
from .exact import ExactMetricVerifier


class VerifierRouter:
    """Choose deterministic verification or report why none applies."""

    def __init__(self, metric_definitions: Dict[str, MetricDefinition]) -> None:
        self._metric_definitions = metric_definitions
        self._resolver = EvidenceResolver()
        self._exact = ExactMetricVerifier()

    def verify(
        self, claim: Claim, packet: EvidencePacket, artifacts: Sequence[Artifact]
    ) -> ClaimVerificationResult:
        if claim.claim_type == "metric":
            metric_key = claim.normalized.get("metric_key")
            definition = self._metric_definitions.get(metric_key)
            if definition is not None:
                return self._exact.verify(
                    claim, self._resolver.resolve(claim, packet, artifacts), definition
                )
        return ClaimVerificationResult(
            claim_id=claim.claim_id,
            status="unverifiable",
            verification_method="exact_match",
            evidence_artifact_ids=[],
            verifier_name="deterministic-router",
            verifier_version="1",
            reason_code="NO_DETERMINISTIC_VERIFIER",
        )


__all__ = ["VerifierRouter"]
