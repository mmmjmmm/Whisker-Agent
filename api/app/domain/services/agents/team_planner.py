import json

from app.domain.models.event import ErrorEvent, MessageEvent
from app.domain.models.message import Message
from app.domain.models.team import PlannedTaskGraph
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.team import PLANNER_SYSTEM_PROMPT


class TeamPlannerAgent(BaseAgent):
    name = "team_planner"
    _system_prompt = PLANNER_SYSTEM_PROMPT
    _format = "json_object"
    _tool_choice = "none"

    async def create_graph(
        self,
        message: Message,
        validation_error: str | None = None,
    ) -> PlannedTaskGraph:
        query = json.dumps(
            {
                "goal": message.message,
                "attachments": message.attachments,
                "previous_validation_error": validation_error,
            },
            ensure_ascii=False,
        )
        async for event in self.invoke(query):
            if isinstance(event, ErrorEvent):
                raise RuntimeError(event.error)
            if isinstance(event, MessageEvent):
                parsed = await self._json_parser.invoke(event.message)
                return PlannedTaskGraph.model_validate(parsed)
        raise RuntimeError("planner produced no graph")
