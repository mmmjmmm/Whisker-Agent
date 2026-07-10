import asyncio
import json

import pytest

from app.domain.models.agent_run import AgentMode
from app.domain.models.event import DoneEvent, MessageEvent
from app.domain.models.run_command import StartRunCommand
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.flows.base import FlowResourceRequirements
from app.domain.services.flows.flow_router import FlowRouter


class FakeQueue:
    def __init__(self, messages=None) -> None:
        self.messages = list(messages or [])
        self.output_types: list[str] = []

    async def pop(self):
        return "input-1", self.messages.pop(0)

    async def put(self, message):
        event = json.loads(message)
        self.output_types.append(event["type"])
        return f"output-{len(self.output_types)}"

    async def is_empty(self) -> bool:
        return not self.messages


class FakeTask:
    def __init__(self, messages) -> None:
        self.input_stream = FakeQueue(messages)
        self.output_stream = FakeQueue()


class FakeSessionRepository:
    def __init__(self) -> None:
        self.events = []
        self.statuses = []

    async def add_event(self, session_id, event) -> None:
        self.events.append((session_id, event))

    async def update_status(self, session_id, status) -> None:
        self.statuses.append((session_id, status))

    async def update_title(self, *_args) -> None:
        return None

    async def update_latest_message(self, *_args) -> None:
        return None

    async def increment_unread_message_count(self, *_args) -> None:
        return None


class FakeUow:
    def __init__(self, session) -> None:
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class FakeSandbox:
    def __init__(self) -> None:
        self.ensure_count = 0

    async def ensure_sandbox(self) -> None:
        self.ensure_count += 1

    async def destroy(self) -> None:
        return None


class FakeMCPTool:
    def __init__(self) -> None:
        self.initialize_count = 0
        self.cleanup_count = 0

    async def initialize(self, _config) -> None:
        self.initialize_count += 1

    async def cleanup(self) -> None:
        self.cleanup_count += 1


class FakeA2AManager:
    def __init__(self) -> None:
        self.cleanup_count = 0

    async def cleanup(self) -> None:
        self.cleanup_count += 1


class FakeA2ATool:
    def __init__(self) -> None:
        self.initialize_count = 0
        self.manager = FakeA2AManager()

    async def initialize(self, _config) -> None:
        self.initialize_count += 1


class FakeFlow:
    resource_requirements = FlowResourceRequirements()

    def __init__(self, *, cancel_after_done: bool = False) -> None:
        self.cancel_after_done = cancel_after_done
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        yield DoneEvent(session_id=request.command.session_id)
        if self.cancel_after_done:
            raise asyncio.CancelledError


def build_runner(*, router, sandbox, mcp_tool, a2a_tool, session_repo):
    return AgentTaskRunner(
        uow_factory=lambda: FakeUow(session_repo),
        llm=object(),
        agent_config=object(),
        mcp_config=object(),
        a2a_config=object(),
        session_id="session-1",
        file_storage=object(),
        json_parser=object(),
        browser=object(),
        search_engine=object(),
        sandbox=sandbox,
        flow_router=router,
        mcp_tool=mcp_tool,
        a2a_tool=a2a_tool,
    )


async def test_research_command_does_not_initialize_react_resources() -> None:
    research_flow = FakeFlow()
    router = FlowRouter(
        react_factory=lambda: FakeFlow(),
        research_factory=lambda: research_flow,
    )
    sandbox = FakeSandbox()
    mcp_tool = FakeMCPTool()
    a2a_tool = FakeA2ATool()
    session_repo = FakeSessionRepository()
    runner = build_runner(
        router=router,
        sandbox=sandbox,
        mcp_tool=mcp_tool,
        a2a_tool=a2a_tool,
        session_repo=session_repo,
    )
    command = StartRunCommand(
        run_id="run-1",
        session_id="session-1",
        mode=AgentMode.RESEARCH_TEAM,
        message="research this topic",
    )
    task = FakeTask([command.model_dump_json()])

    await runner.invoke(task)

    assert sandbox.ensure_count == 0
    assert mcp_tool.initialize_count == 0
    assert a2a_tool.initialize_count == 0
    assert task.output_stream.output_types == ["done"]
    assert research_flow.requests[0].command == command


async def test_legacy_message_defaults_to_react_and_initializes_resources() -> None:
    react_flow = FakeFlow()
    router = FlowRouter(
        react_factory=lambda: react_flow,
        research_factory=lambda: FakeFlow(),
    )
    sandbox = FakeSandbox()
    mcp_tool = FakeMCPTool()
    a2a_tool = FakeA2ATool()
    runner = build_runner(
        router=router,
        sandbox=sandbox,
        mcp_tool=mcp_tool,
        a2a_tool=a2a_tool,
        session_repo=FakeSessionRepository(),
    )
    task = FakeTask([MessageEvent(
        role="user",
        message="legacy question",
    ).model_dump_json()])

    await runner.invoke(task)

    assert sandbox.ensure_count == 1
    assert mcp_tool.initialize_count == 1
    assert a2a_tool.initialize_count == 1
    assert react_flow.requests[0].command.mode == AgentMode.REACT
    assert react_flow.requests[0].message.message == "legacy question"


async def test_runner_publishes_done_once_when_cancelled_after_flow_done() -> None:
    research_flow = FakeFlow(cancel_after_done=True)
    router = FlowRouter(
        react_factory=lambda: FakeFlow(),
        research_factory=lambda: research_flow,
    )
    runner = build_runner(
        router=router,
        sandbox=FakeSandbox(),
        mcp_tool=FakeMCPTool(),
        a2a_tool=FakeA2ATool(),
        session_repo=FakeSessionRepository(),
    )
    command = StartRunCommand(
        run_id="run-1",
        session_id="session-1",
        mode=AgentMode.RESEARCH_TEAM,
        message="research this topic",
    )
    task = FakeTask([command.model_dump_json()])

    with pytest.raises(asyncio.CancelledError):
        await runner.invoke(task)

    assert task.output_stream.output_types.count("done") == 1
