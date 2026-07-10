from app.domain.models.agent_run import CapabilityProfile
from app.domain.models.research import ReviewContext, ReviewResult
from app.domain.services.prompts.research import render_reviewer_prompt
from app.domain.services.research.agent_runtime import TeamAgentRuntime


class CoverageReviewerAgent:
    def __init__(self, runtime: TeamAgentRuntime) -> None:
        self.runtime = runtime

    async def review(self, context: ReviewContext) -> ReviewResult:
        return await self.runtime.run(
            prompt=render_reviewer_prompt(context),
            output_type=ReviewResult,
            profile=CapabilityProfile.ANALYSIS,
            memory_key="reviewer",
        )

