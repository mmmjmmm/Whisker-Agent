import json
import posixpath
import re
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any

from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.domain.models.event import (
    BaseEvent,
    ErrorEvent,
    MessageEvent,
    ToolEvent,
    ToolEventStatus,
)
from app.domain.models.team import TeamTask, WorkerResult
from app.domain.services.agents.base import BaseAgent
from app.domain.services.prompts.team import WORKER_SYSTEM_PROMPT

EmitEvent = Callable[[BaseEvent, bool], Awaitable[None]]
URL_RE = re.compile(r"https?://[^\s\]\)\"'<>]+")
HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)
ARTIFACT_ROOT = PurePosixPath("/home/ubuntu")
ARTIFACT_FUNCTIONS = frozenset({"write_file", "replace_in_file"})


def normalize_http_url(value: str) -> str:
    try:
        return str(HTTP_URL_ADAPTER.validate_python(value))
    except ValidationError:
        return value


def collect_urls(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {
            normalize_http_url(match.rstrip(".,;:!?"))
            for match in URL_RE.findall(value)
        }
    if isinstance(value, dict):
        return set().union(*(collect_urls(item) for item in value.values())) if value else set()
    if isinstance(value, (list, tuple, set)):
        return set().union(*(collect_urls(item) for item in value)) if value else set()
    if hasattr(value, "model_dump"):
        return collect_urls(value.model_dump(mode="json"))
    return set()


def validate_sources(result: WorkerResult, observed_urls: set[str]) -> None:
    normalized_observed = {normalize_http_url(url) for url in observed_urls}
    unknown = {
        normalize_http_url(str(source.url)) for source in result.sources
    } - normalized_observed
    if unknown:
        raise ValueError(f"unobserved source URLs: {sorted(unknown)}")


def normalize_artifact_path(value: str) -> str | None:
    """只接受沙箱主目录下的规范绝对路径。"""
    if not value or "\x00" in value:
        return None
    normalized = PurePosixPath(posixpath.normpath(value))
    if not normalized.is_absolute() or normalized == ARTIFACT_ROOT:
        return None
    try:
        normalized.relative_to(ARTIFACT_ROOT)
    except ValueError:
        return None
    return str(normalized)


def validate_artifacts(
    result: WorkerResult,
    observed_artifacts: set[str],
) -> None:
    normalized_observed = {
        normalized
        for artifact in observed_artifacts
        if (normalized := normalize_artifact_path(artifact)) is not None
    }
    unsafe = {
        artifact
        for artifact in result.artifacts
        if normalize_artifact_path(artifact) is None
    }
    if unsafe:
        raise ValueError(f"unsafe artifact paths: {sorted(unsafe)}")
    unknown = {
        normalize_artifact_path(artifact) for artifact in result.artifacts
    } - normalized_observed
    if unknown:
        raise ValueError(f"unobserved artifact paths: {sorted(unknown)}")


def collect_observed_artifacts(event: ToolEvent) -> set[str]:
    """从本次成功文件写入的结构化结果中提取产物，而非相信 LLM 自报。"""
    if (
        event.function_name not in ARTIFACT_FUNCTIONS
        or event.function_result is None
        or not event.function_result.success
    ):
        return set()
    data = event.function_result.data
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    if not isinstance(data, dict):
        return set()
    filepath = data.get("filepath")
    if not isinstance(filepath, str):
        return set()
    normalized = normalize_artifact_path(filepath)
    return {normalized} if normalized else set()


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
        observed_urls: set[str] = set()
        observed_artifacts: set[str] = set()
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
                await emit(event, True)
                if (
                    event.status is ToolEventStatus.CALLED
                    and event.function_result is not None
                    and event.function_result.success
                ):
                    observed_urls.update(collect_urls(event.function_args))
                    observed_urls.update(collect_urls(event.function_result))
                    observed_urls.update(collect_urls(event.tool_content))
                    observed_artifacts.update(collect_observed_artifacts(event))
            elif isinstance(event, ErrorEvent):
                raise RuntimeError(event.error)
            elif isinstance(event, MessageEvent):
                parsed = await self._json_parser.invoke(event.message)
                result = WorkerResult.model_validate(parsed)
                validate_sources(result, observed_urls)
                validate_artifacts(result, observed_artifacts)
                return result
        raise RuntimeError("worker produced no result")
