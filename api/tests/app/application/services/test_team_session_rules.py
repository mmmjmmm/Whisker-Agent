import asyncio

import pytest
from pydantic import TypeAdapter

from app.application.errors.exceptions import ConflictError
from app.application.services.agent_service import AgentService
from app.application.services.session_service import SessionService
from app.domain.models.event import (
    Event,
    MessageEvent,
    TaskGraphEvent,
    TeamTaskEvent,
)
from app.domain.models.session import Session, SessionStatus
from app.domain.models.team import (
    AgentMode,
    PlannedTask,
    PlannedTaskGraph,
    TaskGraphStatus,
    TeamCapability,
    TeamTaskStatus,
)
from app.domain.services.team.graph import build_task_graph
from app.interfaces.schemas.session import ChatRequest


class InMemorySessionRepository:
    def __init__(self, session):
        self.value = session
        self.persisted_events = []

    async def get_by_id(self, session_id):
        return self.value if self.value.id == session_id else None

    async def get_all(self):
        return [self.value]

    async def add_event(self, session_id, event):
        self.persisted_events.append(event)

    async def update_status(self, session_id, status):
        self.value.status = status

    async def update_latest_message(self, session_id, message, timestamp):
        self.value.latest_message = message
        self.value.latest_message_at = timestamp

    async def update_unread_message_count(self, session_id, count):
        self.value.unread_message_count = count


class FakeFileRepository:
    async def get_by_id(self, file_id):
        return None


class FakeUow:
    def __init__(self, session):
        self.session = InMemorySessionRepository(session)
        self.file = FakeFileRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def commit(self):
        pass

    async def rollback(self):
        pass


class MissingTaskRegistry:
    @classmethod
    def get(cls, task_id):
        return None


def running_team_session(graph_status=TaskGraphStatus.RUNNING):
    graph = build_task_graph(
        PlannedTaskGraph(
            title="team",
            goal="work",
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
    graph.status = graph_status
    graph.tasks[0].status = (
        TeamTaskStatus.COMPLETED
        if graph_status is TaskGraphStatus.COMPLETED
        else TeamTaskStatus.RUNNING
    )
    return Session(
        id="session",
        task_id="missing-task",
        status=SessionStatus.RUNNING,
        events=[
            MessageEvent(role="user", message="go", agent_mode=AgentMode.TEAM),
            TaskGraphEvent(graph=graph.model_copy(deep=True)),
            TeamTaskEvent(
                graph_id=graph.id,
                task=graph.tasks[0].model_copy(deep=True),
                agent_id="worker-1",
                attempt=1,
            ),
        ],
    )


def test_chat_request_defaults_to_react():
    assert ChatRequest(message="x").mode is AgentMode.REACT


def test_running_team_session_rejects_new_message():
    async def scenario():
        session = running_team_session()
        uow = FakeUow(session)
        service = object.__new__(AgentService)
        service._uow = uow

        with pytest.raises(ConflictError):
            await service.validate_chat_request(
                "session",
                AgentMode.TEAM,
                has_message=True,
            )

        await service.validate_chat_request(
            "session",
            AgentMode.TEAM,
            has_message=False,
        )

    asyncio.run(scenario())


def test_missing_task_registry_marks_team_graph_interrupted():
    async def scenario():
        stored = running_team_session()
        uow = FakeUow(stored)
        service = SessionService(
            uow_factory=lambda: uow,
            sandbox_cls=object,
            task_cls=MissingTaskRegistry,
        )

        session = await service.get_session("session")
        graph = session.get_latest_task_graph()

        assert session.status is SessionStatus.COMPLETED
        assert graph.status is TaskGraphStatus.FAILED
        assert graph.error == "process_interrupted"
        assert graph.task_by_id("a").status is TeamTaskStatus.FAILED
        assert [event.type for event in uow.session.persisted_events] == [
            "task",
            "task_graph",
        ]

    asyncio.run(scenario())


def test_terminal_graph_only_repairs_session_status_after_restart():
    async def scenario():
        stored = running_team_session(TaskGraphStatus.COMPLETED)
        uow = FakeUow(stored)
        service = SessionService(
            uow_factory=lambda: uow,
            sandbox_cls=object,
            task_cls=MissingTaskRegistry,
        )

        session = await service.get_session("session")
        graph = session.get_latest_task_graph()

        assert session.status is SessionStatus.COMPLETED
        assert graph.status is TaskGraphStatus.COMPLETED
        assert uow.session.persisted_events == []

    asyncio.run(scenario())


def test_latest_team_graph_resets_at_new_user_turn():
    session = running_team_session(TaskGraphStatus.COMPLETED)
    session.events.append(
        MessageEvent(
            role="user",
            message="new run",
            agent_mode=AgentMode.TEAM,
        )
    )

    assert session.get_latest_task_graph() is None


def test_interrupted_team_without_current_graph_converges_to_error():
    async def scenario():
        session = running_team_session(TaskGraphStatus.COMPLETED)
        session.events.append(
            MessageEvent(
                role="user",
                message="new run",
                agent_mode=AgentMode.TEAM,
            )
        )
        session.status = SessionStatus.RUNNING
        session.task_id = "missing-task"
        uow = FakeUow(session)
        service = SessionService(
            uow_factory=lambda: uow,
            sandbox_cls=object,
            task_cls=MissingTaskRegistry,
        )

        repaired = await service.get_session("session")

        assert repaired.status is SessionStatus.COMPLETED
        assert [event.type for event in uow.session.persisted_events] == [
            "error"
        ]
        assert (
            uow.session.persisted_events[0].error
            == "Team 运行因进程中断而终止: process_interrupted"
        )

    asyncio.run(scenario())


class FakeInputStream:
    def __init__(self):
        self.values = []

    async def put(self, value):
        self.values.append(value)
        return "1-0"


class FinishedTask:
    def __init__(self):
        self.input_stream = FakeInputStream()
        self.invoked = False

    @property
    def done(self):
        return True

    async def invoke(self):
        self.invoked = True

    async def wait(self):
        pass


def test_agent_service_writes_mode_into_user_input_event():
    async def scenario():
        session = Session(
            id="session",
            task_id="task-1",
            status=SessionStatus.RUNNING,
        )
        uow = FakeUow(session)
        task = FinishedTask()

        class TaskRegistry:
            @classmethod
            def get(cls, task_id):
                return task

        service = object.__new__(AgentService)
        service._uow = uow
        service._uow_factory = lambda: uow
        service._task_cls = TaskRegistry
        events = [
            event
            async for event in service.chat(
                "session",
                message="research",
                attachments=[],
                mode=AgentMode.TEAM,
            )
        ]
        await asyncio.sleep(0)

        queued = TypeAdapter(Event).validate_json(task.input_stream.values[0])
        assert events[0].agent_mode is AgentMode.TEAM
        assert queued.agent_mode is AgentMode.TEAM
        assert task.invoked

    asyncio.run(scenario())


class RunningTask(FinishedTask):
    def __init__(self):
        super().__init__()
        self.cancelled = False
        self.wait_started = asyncio.Event()
        self.allow_finish = asyncio.Event()

    @property
    def done(self):
        return False

    def cancel(self):
        self.cancelled = True
        return True

    async def wait(self):
        self.wait_started.set()
        await self.allow_finish.wait()


def test_stop_session_waits_for_runner_cleanup_before_returning():
    async def scenario():
        session = running_team_session()
        uow = FakeUow(session)
        task = RunningTask()

        class TaskRegistry:
            @classmethod
            def get(cls, task_id):
                return task

        service = object.__new__(AgentService)
        service._uow = uow
        service._task_cls = TaskRegistry

        stopping = asyncio.create_task(service.stop_session("session"))
        await asyncio.sleep(0)

        assert task.cancelled
        assert task.wait_started.is_set()
        assert not stopping.done()
        assert session.status is SessionStatus.RUNNING

        task.allow_finish.set()
        await stopping

        assert session.status is SessionStatus.COMPLETED

    asyncio.run(scenario())


class PreparedTask(FinishedTask):
    def __init__(self, task_id):
        super().__init__()
        self.id = task_id


def test_concurrent_team_prepare_creates_one_run_and_rejects_the_other():
    async def scenario():
        session = Session(id="session", status=SessionStatus.PENDING)
        uow = FakeUow(session)
        tasks = {}
        create_started = asyncio.Event()
        allow_create = asyncio.Event()
        create_count = 0

        class TaskRegistry:
            @classmethod
            def get(cls, task_id):
                return tasks.get(task_id)

        service = object.__new__(AgentService)
        service._uow = uow
        service._uow_factory = lambda: uow
        service._task_cls = TaskRegistry

        async def create_task(current):
            nonlocal create_count
            create_count += 1
            create_started.set()
            await allow_create.wait()
            task = PreparedTask(f"task-{create_count}")
            tasks[task.id] = task
            current.task_id = task.id
            return task

        service._create_task = create_task

        first = asyncio.create_task(
            service.prepare_chat(
                "session",
                "first",
                [],
                AgentMode.TEAM,
            )
        )
        await create_started.wait()
        second = asyncio.create_task(
            service.prepare_chat(
                "session",
                "second",
                [],
                AgentMode.TEAM,
            )
        )
        await asyncio.sleep(0)
        allow_create.set()

        first_result, second_result = await asyncio.gather(
            first,
            second,
            return_exceptions=True,
        )

        assert not isinstance(first_result, Exception)
        assert isinstance(second_result, ConflictError)
        assert create_count == 1
        assert session.status is SessionStatus.RUNNING
        assert len(uow.session.persisted_events) == 1
        assert uow.session.persisted_events[0].agent_mode is AgentMode.TEAM

    asyncio.run(scenario())
