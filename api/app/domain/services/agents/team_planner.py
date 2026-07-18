import json
from collections.abc import Awaitable, Callable

from app.domain.models.event import BaseEvent, ErrorEvent, MessageEvent, ToolEvent
from app.domain.models.message import Message
from app.domain.models.team import PlannedTaskGraph, TaskGraph
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.team import PLANNER_SYSTEM_PROMPT
from app.domain.services.team.graph import build_task_graph


class TeamPlannerAgent(BaseAgent):
    name = "team_planner"
    _system_prompt = PLANNER_SYSTEM_PROMPT
    _format = "json_object"
    _tool_choice = "none"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._skill_events: list[BaseEvent] = []

    async def create_graph(
        self,
        message: Message,
        validation_error: str | None = None,
        emit: Callable[[BaseEvent], Awaitable[None]] | None = None,
        *,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> TaskGraph:
        async with self._trace_agent_operation(
                name="team_planner.create_graph",
                operation="create_graph",
                input={
                    "message": message.message,
                    "attachments": message.attachments,
                    "previous_validation_error": validation_error,
                },
                attempt=attempt,
                max_attempts=max_attempts,
        ) as trace_scope:
            query = json.dumps(
                {
                    "goal": message.message,
                    "attachments": message.attachments,
                    "previous_validation_error": validation_error,
                },
                ensure_ascii=False,
            )
            async for event in self.invoke(query):
                if isinstance(event, ToolEvent):
                    self._skill_events.append(event)
                    if emit is not None:
                        await emit(event)
                    continue
                if isinstance(event, ErrorEvent):
                    raise RuntimeError(event.error)
                if isinstance(event, MessageEvent):
                    parsed = await self._json_parser.invoke(event.message)
                    planned = PlannedTaskGraph.model_validate(parsed)
                    graph = build_task_graph(
                        planned,
                        self._agent_config.team_max_tasks,
                    )
                    if trace_scope is not None:
                        trace_scope.finish(output=graph.model_dump(mode="json"))
                    return graph
            raise RuntimeError("planner produced no graph")

    def drain_skill_events(self) -> list[BaseEvent]:
        events = self._skill_events
        self._skill_events = []
        return events
