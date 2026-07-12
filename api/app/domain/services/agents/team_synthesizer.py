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
    ) -> FinalTeamResponse:
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
                return FinalTeamResponse.model_validate(parsed)
        raise RuntimeError("synthesizer produced no response")

    def drain_skill_events(self) -> list[BaseEvent]:
        events = self._skill_events
        self._skill_events = []
        return events
