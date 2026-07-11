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
