from app.domain.models.event import ErrorEvent, MessageEvent
from app.domain.models.team import FinalTeamResponse, TaskGraph
from app.domain.services.agents.base import BaseAgent
from app.domain.services.agents.task_worker import collect_urls, normalize_http_url
from app.domain.services.prompts.team import SYNTHESIZER_SYSTEM_PROMPT


def validate_final_links(message: str, allowed_urls: set[str]) -> None:
    normalized_allowed = {normalize_http_url(url) for url in allowed_urls}
    unknown = collect_urls(message) - normalized_allowed
    if unknown:
        raise ValueError(f"unknown source URLs: {sorted(unknown)}")


def validate_final_attachments(
    attachments: list[str],
    allowed_artifacts: set[str],
) -> None:
    unknown = set(attachments) - allowed_artifacts
    if unknown:
        raise ValueError(f"unknown attachments: {sorted(unknown)}")


class TeamSynthesizerAgent(BaseAgent):
    name = "team_synthesizer"
    _system_prompt = SYNTHESIZER_SYSTEM_PROMPT
    _format = "json_object"
    _tool_choice = "none"

    async def synthesize(self, graph: TaskGraph) -> FinalTeamResponse:
        allowed_urls = {
            str(source.url)
            for task in graph.tasks
            if task.result
            for source in task.result.sources
        }
        allowed_artifacts = {
            artifact
            for task in graph.tasks
            if task.result
            for artifact in task.result.artifacts
        }
        async for event in self.invoke(graph.model_dump_json()):
            if isinstance(event, ErrorEvent):
                raise RuntimeError(event.error)
            if isinstance(event, MessageEvent):
                parsed = await self._json_parser.invoke(event.message)
                response = FinalTeamResponse.model_validate(parsed)
                validate_final_links(response.message, allowed_urls)
                validate_final_attachments(
                    response.attachments,
                    allowed_artifacts,
                )
                return response
        raise RuntimeError("synthesizer produced no response")
