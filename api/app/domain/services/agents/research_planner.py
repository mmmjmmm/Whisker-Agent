from app.domain.models.agent_run import CapabilityProfile, RunBudget
from app.domain.models.research import ResearchPlan
from app.domain.services.prompts.research import (
    render_plan_repair_prompt,
    render_planner_prompt,
)
from app.domain.services.research.agent_runtime import TeamAgentRuntime
from app.domain.services.research.task_graph import InvalidTaskGraph


class ResearchPlannerAgent:
    def __init__(self, runtime: TeamAgentRuntime) -> None:
        self.runtime = runtime

    async def plan(self, goal: str, budget: RunBudget) -> ResearchPlan:
        return await self.runtime.run(
            prompt=render_planner_prompt(goal, budget),
            output_type=ResearchPlan,
            profile=CapabilityProfile.ANALYSIS,
            memory_key="planner",
        )

    async def repair_invalid_plan(
            self,
            goal: str,
            error: InvalidTaskGraph,
            budget: RunBudget,
    ) -> ResearchPlan:
        return await self.runtime.run(
            prompt=render_plan_repair_prompt(
                goal,
                budget,
                error.code,
                error.details,
            ),
            output_type=ResearchPlan,
            profile=CapabilityProfile.ANALYSIS,
            memory_key="planner",
        )

