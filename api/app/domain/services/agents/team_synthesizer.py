from app.domain.models.event import ErrorEvent, MessageEvent
from app.domain.models.team import FinalTeamResponse, TaskGraph
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.team import SYNTHESIZER_SYSTEM_PROMPT


class TeamSynthesizerAgent(BaseAgent):
    name = "team_synthesizer"
    _system_prompt = SYNTHESIZER_SYSTEM_PROMPT
    _format = "json_object"
    _tool_choice = "none"

    async def synthesize(self, graph: TaskGraph) -> FinalTeamResponse:
        async for event in self.invoke(graph.model_dump_json()):
            if isinstance(event, ErrorEvent):
                raise RuntimeError(event.error)
            if isinstance(event, MessageEvent):
                parsed = await self._json_parser.invoke(event.message)
                return FinalTeamResponse.model_validate(parsed)
        raise RuntimeError("synthesizer produced no response")
