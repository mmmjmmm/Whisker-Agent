from collections.abc import Awaitable, Callable

from app.domain.models.event import BaseEvent, ErrorEvent, MessageEvent, ToolEvent
from app.domain.models.team import FinalTeamResponse, TaskGraph
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.team import SYNTHESIZER_SYSTEM_PROMPT


class TeamSynthesizerAgent(BaseAgent):
    name = "team_synthesizer"
    _system_prompt = SYNTHESIZER_SYSTEM_PROMPT
    _format = "json_object"
    _tool_choice = "none"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._skill_events: list[BaseEvent] = []

    async def synthesize(
        self,
        graph: TaskGraph,
        emit: Callable[[BaseEvent], Awaitable[None]] | None = None,
        *,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> FinalTeamResponse:
        async with self._trace_agent_operation(
                name="team_synthesizer.synthesize",
                operation="synthesize",
                input=graph.model_dump(mode="json"),
                attributes={
                    "graph_id": graph.id,
                    "graph_status": graph.status.value,
                },
                attempt=attempt,
                max_attempts=max_attempts,
        ) as trace_scope:
            async for event in self.invoke(graph.model_dump_json()):
                if isinstance(event, ToolEvent):
                    self._skill_events.append(event)
                    if emit is not None:
                        await emit(event)
                    continue
                if isinstance(event, ErrorEvent):
                    raise RuntimeError(event.error)
                if isinstance(event, MessageEvent):
                    parsed = await self._json_parser.invoke(event.message)
                    response = FinalTeamResponse.model_validate(parsed)
                    if trace_scope is not None:
                        trace_scope.finish(output=response.model_dump(mode="json"))
                    return response
            raise RuntimeError("synthesizer produced no response")

    def drain_skill_events(self) -> list[BaseEvent]:
        events = self._skill_events
        self._skill_events = []
        return events
