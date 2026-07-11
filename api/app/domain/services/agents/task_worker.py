import json
from collections.abc import Awaitable, Callable

from app.domain.models.event import (
    BaseEvent,
    ErrorEvent,
    MessageEvent,
    ToolEvent,
)
from app.domain.models.team import TeamTask, WorkerResult
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.team import WORKER_SYSTEM_PROMPT

EmitEvent = Callable[[BaseEvent], Awaitable[None]]


class TaskWorker(BaseAgent):
    name = "task_worker"
    _system_prompt = WORKER_SYSTEM_PROMPT
    _format = "json_object"

    def __init__(
        self,
        *args,
        graph_id: str,
        task: TeamTask,
        agent_id: str,
        attempt: int,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._graph_id = graph_id
        self._task = task
        self._agent_id = agent_id
        self._attempt = attempt

    async def execute(
        self,
        *,
        goal: str,
        dependency_results: dict[str, WorkerResult],
        attachments: list[str],
        emit: EmitEvent,
    ) -> WorkerResult:
        query = json.dumps(
            {
                "goal": goal,
                "task": self._task.model_dump(
                    mode="json",
                    include={
                        "id",
                        "description",
                        "dependencies",
                        "capability",
                        "success_criteria",
                    },
                ),
                "dependency_results": {
                    key: value.model_dump(mode="json")
                    for key, value in dependency_results.items()
                },
                "attachments": attachments,
            },
            ensure_ascii=False,
        )

        async for event in self.invoke(query):
            if isinstance(event, ToolEvent):
                event.graph_id = self._graph_id
                event.task_id = self._task.id
                event.agent_id = self._agent_id
                event.attempt = self._attempt
                await emit(event)
            elif isinstance(event, ErrorEvent):
                raise RuntimeError(event.error)
            elif isinstance(event, MessageEvent):
                parsed = await self._json_parser.invoke(event.message)
                return WorkerResult.model_validate(parsed)
        raise RuntimeError("worker produced no result")
