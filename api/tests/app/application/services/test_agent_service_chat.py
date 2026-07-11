import json

import pytest

from app.application.errors.exceptions import ResearchTeamDisabledError
from app.application.services.agent_service import AgentService
from app.domain.models.agent_run import AgentMode, AgentRun, RunStatus
from app.domain.models.session import Session


class FakeQueue:
    def __init__(self) -> None:
        self.messages = []

    async def put(self, message) -> str:
        self.messages.append(message)
        return f"input-{len(self.messages)}"


class FakeTask:
    registry = {}
    created_count = 0

    def __init__(self, task_runner) -> None:
        type(self).created_count += 1
        self.id = f"task-{type(self).created_count}"
        self.input_stream = FakeQueue()
        self.output_stream = FakeQueue()
        self.done = False
        self.invoke_count = 0
        self.task_runner = task_runner
        type(self).registry[self.id] = self

    async def invoke(self) -> None:
        self.invoke_count += 1

    def cancel(self) -> bool:
        self.done = True
        return True

    @classmethod
    def get(cls, task_id):
        return cls.registry.get(task_id)

    @classmethod
    def create(cls, task_runner):
        return cls(task_runner)


class FakeSandbox:
    create_count = 0

    @classmethod
    async def get(cls, _sandbox_id):
        return None

    @classmethod
    async def create(cls):
        cls.create_count += 1
        raise AssertionError("team mode must not create a sandbox")


class FakeSessionRepository:
    def __init__(self, session: Session) -> None:
        self.sessions = {session.id: session}
        self.events = []

    async def get_by_id(self, session_id):
        return self.sessions.get(session_id)

    async def save(self, session) -> None:
        self.sessions[session.id] = session

    async def update_latest_message(self, session_id, message, timestamp) -> None:
        session = self.sessions[session_id]
        session.latest_message = message
        session.latest_message_at = timestamp

    async def add_event(self, session_id, event) -> None:
        self.events.append((session_id, event))


class FakeRunRepository:
    terminal = {
        RunStatus.COMPLETED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
    }

    def __init__(self) -> None:
        self.runs = {}

    async def add(self, run) -> None:
        self.runs[run.id] = run

    async def get_active_by_session(self, session_id):
        return next((
            run for run in self.runs.values()
            if run.session_id == session_id and run.status not in self.terminal
        ), None)


class FakeFileRepository:
    async def get_by_id(self, _file_id):
        return None


class FakeUow:
    def __init__(self, session, agent_run, file) -> None:
        self.session = session
        self.agent_run = agent_run
        self.file = file

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


def build_service(*, enabled: bool = True):
    FakeTask.registry = {}
    FakeTask.created_count = 0
    FakeSandbox.create_count = 0
    session = Session(id="session-1")
    sessions = FakeSessionRepository(session)
    runs = FakeRunRepository()
    files = FakeFileRepository()
    uow_factory = lambda: FakeUow(sessions, runs, files)
    service = AgentService(
        uow_factory=uow_factory,
        llm=object(),
        agent_config=object(),
        mcp_config=object(),
        a2a_config=object(),
        sandbox_cls=FakeSandbox,
        task_cls=FakeTask,
        json_parser=object(),
        search_engine=object(),
        file_storage=object(),
        research_team_enabled=enabled,
        research_flow_factory=lambda _session_id: object(),
    )
    return service, session, sessions, runs


async def test_new_team_run_does_not_create_sandbox() -> None:
    service, session, sessions, runs = build_service()

    prepared = await service.prepare_chat(
        session.id,
        message="compare the approaches",
        mode=AgentMode.RESEARCH_TEAM,
    )

    assert prepared.created_task is True
    assert FakeSandbox.create_count == 0
    assert prepared.command.mode == AgentMode.RESEARCH_TEAM
    assert runs.runs[prepared.command.run_id].status == RunStatus.PENDING
    assert prepared.initial_events[0].run_id == prepared.command.run_id
    queued = json.loads(prepared.task.input_stream.messages[0])
    assert queued["command_type"] == "start"
    assert queued["mode"] == "research_team"
    assert sessions.events[0][1].run_id == prepared.command.run_id


async def test_disabled_team_mode_fails_before_task_creation() -> None:
    service, session, _sessions, runs = build_service(enabled=False)

    with pytest.raises(ResearchTeamDisabledError):
        await service.prepare_chat(
            session.id,
            message="research",
            mode=AgentMode.RESEARCH_TEAM,
        )

    assert FakeTask.created_count == 0
    assert runs.runs == {}
