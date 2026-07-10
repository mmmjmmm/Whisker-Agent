from app.domain.models.agent_run import CapabilityProfile
from app.domain.models.research import (
    ClaimSupportStatus,
    DraftReport,
    EvidenceExcerpt,
    ResearchClaim,
    ReviewResult,
)
from app.domain.services.prompts.research import render_synthesizer_prompt
from app.domain.services.research.agent_runtime import TeamAgentRuntime


class ResearchSynthesizerAgent:
    def __init__(self, runtime: TeamAgentRuntime) -> None:
        self.runtime = runtime

    async def synthesize(
            self,
            claims: list[ResearchClaim],
            evidence: list[EvidenceExcerpt],
            review: ReviewResult,
    ) -> DraftReport:
        verified = [
            claim
            for claim in claims
            if claim.support_status in {
                ClaimSupportStatus.SUPPORTED,
                ClaimSupportStatus.PARTIALLY_SUPPORTED,
            }
        ]
        referenced_evidence = {
            evidence_id
            for claim in verified
            for evidence_id in claim.evidence_ids
        }
        relevant_evidence = [
            item for item in evidence if item.id in referenced_evidence
        ]
        return await self.runtime.run(
            prompt=render_synthesizer_prompt(
                verified,
                relevant_evidence,
                review,
            ),
            output_type=DraftReport,
            profile=CapabilityProfile.ANALYSIS,
            memory_key="synthesizer",
        )
