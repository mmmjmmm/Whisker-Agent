import asyncio

from app.domain.models.app_config import AgentConfig
from app.domain.models.event import DoneEvent, TaskGraphEvent, TeamTaskEvent
from app.domain.models.message import Message
from app.domain.models.team import (
    AgentMode,
    PlannedTask,
    PlannedTaskGraph,
    TaskGraphStatus,
    TeamCapability,
    TeamTaskStatus,
)
from app.domain.models.tool_result import ToolResult
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.flows.router import FlowRouter
from app.domain.services.flows.team import build_team_flow
from app.domain.services.team.graph import build_task_graph
from app.domain.services.tools.base import BaseTool, tool


async def collect(stream):
    return [item async for item in stream]


class FakeFlow:
    def __init__(self, cancel_events=None):
        self.invocations = 0
        self._cancel_events = cancel_events or []

    async def invoke(self, message):
        self.invocations += 1
        yield DoneEvent()

    async def cancel_events(self):
        return self._cancel_events


def test_runner_routes_modes_without_changing_react_default():
    async def scenario():
        react = FakeFlow()
        team = FakeFlow()
        runner = object.__new__(AgentTaskRunner)
        runner._flow_router = FlowRouter(react, lambda: team)
        runner._active_flow = None

        await collect(runner._run_flow(Message(message="r")))
        await collect(runner._run_flow(Message(message="t"), AgentMode.TEAM))

        assert react.invocations == 1
        assert team.invocations == 1

    asyncio.run(scenario())


def cancelled_graph_events():
    graph = build_task_graph(
        PlannedTaskGraph(
            title="cancel",
            goal="cancel",
            tasks=[
                PlannedTask(
                    id="a",
                    description="a",
                    capability=TeamCapability.SEARCH,
                    success_criteria="done",
                )
            ],
        ),
        max_tasks=5,
    )
    graph.tasks[0].status = TeamTaskStatus.CANCELLED
    graph.status = TaskGraphStatus.CANCELLED
    return [
        TeamTaskEvent(graph_id=graph.id, task=graph.tasks[0], attempt=1),
        TaskGraphEvent(graph=graph),
    ]


def test_runner_persists_cancel_snapshot_before_done():
    async def scenario():
        persisted = []
        runner = object.__new__(AgentTaskRunner)
        runner._active_flow = FakeFlow(cancelled_graph_events())

        async def record(task, event):
            persisted.append(event)

        runner._put_and_add_event = record
        await runner._persist_cancellation(object())

        assert [event.type for event in persisted] == ["task", "task_graph", "done"]

    asyncio.run(scenario())


class FakeSessionRepository:
    async def update_status(self, session_id, status):
        pass


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


class MCPFixtureTool(BaseTool):
    name = "mcp"

    @tool(name="mcp_demo", description="mcp", parameters={}, required=[])
    async def mcp_demo(self):
        return ToolResult()


class A2AFixtureTool(BaseTool):
    name = "a2a"

    @tool(name="a2a_demo", description="a2a", parameters={}, required=[])
    async def a2a_demo(self):
        return ToolResult()


def test_team_flow_factory_keeps_all_operational_tool_capabilities():
    uow = FakeUow()
    flow = build_team_flow(
        uow_factory=lambda: uow,
        session_id="session-1",
        agent_config=AgentConfig(),
        llm=object(),
        json_parser=object(),
        browser=object(),
        sandbox=object(),
        search_engine=object(),
        mcp_tool=MCPFixtureTool(),
        a2a_tool=A2AFixtureTool(),
    )
    expected = {
        TeamCapability.ANALYSIS: set(),
        TeamCapability.SEARCH: {"search_web"},
        TeamCapability.FILE_READ: {"read_file", "search_in_file", "find_files"},
        TeamCapability.FILE_WRITE: {
            "read_file",
            "search_in_file",
            "find_files",
            "write_file",
            "replace_in_file",
        },
        TeamCapability.BROWSER: {"browser_view"},
        TeamCapability.SHELL: {"shell_execute"},
        TeamCapability.MCP: {"mcp_demo"},
        TeamCapability.A2A: {"a2a_demo"},
    }

    for capability, required_names in expected.items():
        task = build_task_graph(
            PlannedTaskGraph(
                title="t",
                goal="g",
                tasks=[
                    PlannedTask(
                        id="a",
                        description="a",
                        capability=capability,
                        success_criteria="done",
                    )
                ],
            ),
            max_tasks=5,
        ).tasks[0]
        worker = flow._orchestrator._worker_factory(
            "graph-1", "worker-1", task, 1
        )
        names = {
            schema["function"]["name"] for schema in worker._get_available_tools()
        }

        assert required_names.issubset(names)
        assert "message_ask_user" not in names
