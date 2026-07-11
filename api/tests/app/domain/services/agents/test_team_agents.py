import asyncio
import json

import pytest

from app.domain.models.app_config import AgentConfig
from app.domain.models.event import ToolEventStatus
from app.domain.models.memory import Memory
from app.domain.models.message import Message
from app.domain.models.team import (
    PlannedTask,
    PlannedTaskGraph,
    SourceRef,
    TeamTaskStatus,
    WorkerResult,
)
from app.domain.models.tool_result import ToolResult
from app.domain.services.agents.task_worker import (
    TaskWorker,
    validate_artifacts,
    validate_sources,
)
from app.domain.services.agents.team_planner import TeamPlannerAgent
from app.domain.services.agents.team_synthesizer import (
    TeamSynthesizerAgent,
    validate_final_attachments,
    validate_final_links,
)
from app.domain.services.team.graph import build_task_graph
from app.domain.services.tools.base import BaseTool, tool


def test_worker_rejects_unobserved_source_url():
    result = WorkerResult(
        success=True,
        summary="finding",
        sources=[SourceRef(title="invented", url="https://invented.example/item")],
    )

    with pytest.raises(ValueError, match="unobserved"):
        validate_sources(result, {"https://observed.example/item"})


def test_worker_rejects_unobserved_or_unsafe_artifact_paths():
    observed = WorkerResult(
        success=True,
        summary="report",
        artifacts=["/home/ubuntu/report.md"],
    )
    validate_artifacts(observed, {"/home/ubuntu/report.md"})

    with pytest.raises(ValueError, match="unobserved artifact"):
        validate_artifacts(observed, set())

    unsafe = WorkerResult(
        success=True,
        summary="secrets",
        artifacts=["/proc/self/environ"],
    )
    with pytest.raises(ValueError, match="unsafe artifact"):
        validate_artifacts(unsafe, {"/proc/self/environ"})


@pytest.mark.parametrize(
    "message",
    [
        "结论 [来源](https://invented.example/item)",
        "结论来自 https://invented.example/item",
    ],
)
def test_synthesizer_rejects_new_links_in_any_text_form(message):
    with pytest.raises(ValueError, match="unknown source"):
        validate_final_links(message, {"https://observed.example/item"})


def test_synthesizer_rejects_unobserved_attachments():
    with pytest.raises(ValueError, match="unknown attachment"):
        validate_final_attachments(
            ["/sandbox/invented.md"],
            {"/sandbox/observed.md"},
        )


class QueueLLM:
    def __init__(self, *responses):
        self.responses = list(responses)

    async def invoke(self, **kwargs):
        return self.responses.pop(0)


class JsonParser:
    async def invoke(self, text, default_value=None):
        return json.loads(text)


class FakeSessionRepository:
    def __init__(self):
        self.save_memory_calls = 0

    async def get_memory(self, session_id, agent_name):
        return Memory()

    async def save_memory(self, session_id, agent_name, memory):
        self.save_memory_calls += 1


class FakeUow:
    def __init__(self):
        self.session = FakeSessionRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def commit(self):
        pass

    async def rollback(self):
        pass


class ObservedSearchTool(BaseTool):
    name = "search"

    @tool(
        name="search_web",
        description="search",
        parameters={"query": {"type": "string"}},
        required=["query"],
    )
    async def search_web(self, query: str):
        return ToolResult(data={"url": "https://observed.example/item"})


def agent_kwargs(llm, tools):
    uow = FakeUow()
    return {
        "uow_factory": lambda: uow,
        "session_id": "session-1",
        "agent_config": AgentConfig(max_iterations=3),
        "llm": llm,
        "json_parser": JsonParser(),
        "tools": tools,
        "memory": Memory(),
        "persist_memory": False,
    }, uow


def test_planner_worker_and_synthesizer_keep_structure_metadata_and_sources():
    async def scenario():
        planner_json = {
            "title": "research",
            "goal": "find a source",
            "tasks": [
                {
                    "id": "collect",
                    "description": "collect one source",
                    "dependencies": [],
                    "capability": "search",
                    "success_criteria": "one observed URL",
                }
            ],
        }
        planner_kwargs, planner_uow = agent_kwargs(
            QueueLLM({"role": "assistant", "content": json.dumps(planner_json)}),
            [],
        )
        planned = await TeamPlannerAgent(**planner_kwargs).create_graph(
            Message(message="research")
        )

        assert planned.tasks[0].id == "collect"
        assert "status" not in type(planned.tasks[0]).model_fields

        graph = build_task_graph(planned, max_tasks=5)
        worker_llm = QueueLLM(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "search_web",
                            "arguments": json.dumps({"query": "multi agent DAG"}),
                        },
                    }
                ],
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "success": True,
                        "summary": "found an observed source",
                        "sources": [
                            {
                                "title": "Observed",
                                "url": "https://observed.example/item",
                            }
                        ],
                        "artifacts": [],
                        "notes": [],
                    }
                ),
            },
        )
        worker_kwargs, worker_uow = agent_kwargs(worker_llm, [ObservedSearchTool()])
        worker = TaskWorker(
            **worker_kwargs,
            allowed_tool_names={"search_web"},
            graph_id=graph.id,
            task=graph.tasks[0],
            agent_id="worker-1",
            attempt=1,
        )
        emitted = []

        async def emit(event, wait_for_publish=True):
            emitted.append(event.model_copy(deep=True))

        result = await worker.execute(
            goal=graph.goal,
            dependency_results={},
            attachments=[],
            emit=emit,
        )

        assert [event.status for event in emitted] == [
            ToolEventStatus.CALLING,
            ToolEventStatus.CALLED,
        ]
        assert {
            (event.graph_id, event.task_id, event.agent_id, event.attempt)
            for event in emitted
        } == {(graph.id, "collect", "worker-1", 1)}
        assert str(result.sources[0].url) == "https://observed.example/item"

        graph.tasks[0].status = TeamTaskStatus.COMPLETED
        graph.tasks[0].result = result
        synthesizer_kwargs, synthesizer_uow = agent_kwargs(
            QueueLLM(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "message": "结论 [Observed](https://observed.example/item)",
                            "attachments": [],
                        }
                    ),
                }
            ),
            [],
        )
        final = await TeamSynthesizerAgent(**synthesizer_kwargs).synthesize(graph)

        assert "https://observed.example/item" in final.message
        assert planner_uow.session.save_memory_calls == 0
        assert worker_uow.session.save_memory_calls == 0
        assert synthesizer_uow.session.save_memory_calls == 0

    asyncio.run(scenario())


def test_worker_rejects_self_reported_artifact_without_tool_evidence():
    async def scenario():
        planned = PlannedTaskGraph(
            title="analysis",
            goal="write a report",
            tasks=[
                PlannedTask(
                    id="analyze",
                    description="analyze",
                    capability="analysis",
                    success_criteria="done",
                )
            ],
        )
        graph = build_task_graph(planned, max_tasks=5)
        worker_kwargs, _ = agent_kwargs(
            QueueLLM(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "success": True,
                            "summary": "claimed a file",
                            "sources": [],
                            "artifacts": ["/home/ubuntu/report.md"],
                            "notes": [],
                        }
                    ),
                }
            ),
            [],
        )
        worker = TaskWorker(
            **worker_kwargs,
            allowed_tool_names=set(),
            graph_id=graph.id,
            task=graph.tasks[0],
            agent_id="worker-1",
            attempt=1,
        )

        async def emit(event, wait_for_publish=True):
            pass

        with pytest.raises(ValueError, match="unobserved artifact"):
            await worker.execute(
                goal=graph.goal,
                dependency_results={},
                attachments=[],
                emit=emit,
            )

    asyncio.run(scenario())
