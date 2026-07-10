from app.domain.models.research import FindingBundle, WorkerContext
from app.domain.services.prompts.research import render_worker_prompt
from app.domain.services.research.agent_runtime import TeamAgentRuntime


class ResearchWorker:
    def __init__(self, runtime: TeamAgentRuntime) -> None:
        self.runtime = runtime

    async def execute(self, context: WorkerContext) -> FindingBundle:
        return await self.runtime.run(
            prompt=render_worker_prompt(context),
            output_type=FindingBundle,
            profile=context.task.capability_profile,
            memory_key=f"worker:{context.task.id}",
        )

