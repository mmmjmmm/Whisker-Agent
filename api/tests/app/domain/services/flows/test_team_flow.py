import asyncio

import pytest

from app.domain.models.event import MessageEvent, TaskGraphEvent, TeamTaskEvent
from app.domain.models.message import Message
from app.domain.models.team import (
    AgentMode,
    FinalTeamResponse,
    PlannedTask,
    PlannedTaskGraph,
    TeamCapability,
    TeamTaskStatus,
    WorkerResult,
)
from app.domain.services.flows.router import FlowRouter
from app.domain.services.flows.team import QueuedEventEmitter, TeamFlow
from app.domain.services.team.graph import build_task_graph, finalize_graph


def valid_plan():
    return PlannedTaskGraph(
        title="research",
        goal="find one result",
        tasks=[
            PlannedTask(
                id="collect",
                description="collect",
                capability=TeamCapability.SEARCH,
                success_criteria="done",
            )
        ],
    )


def make_graph():
    return build_task_graph(valid_plan(), max_tasks=5)


def test_tool_producer_waits_until_event_is_published():
    async def scenario():
        emitter = QueuedEventEmitter()
        reached_after_emit = asyncio.Event()

        async def producer():
            await emitter.emit(TaskGraphEvent(graph=make_graph()), True)
            reached_after_emit.set()

        task = asyncio.create_task(producer())
        envelope = await emitter.get()
        assert envelope is not None
        await asyncio.sleep(0)
        assert not reached_after_emit.is_set()

        envelope.confirm()
        await task

        assert reached_after_emit.is_set()

    asyncio.run(scenario())


class FakeSessionRepository:
    def __init__(self):
        self.statuses = []

    async def update_status(self, session_id, status):
        self.statuses.append(status)


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


class ReplanningPlanner:
    def __init__(self):
        self.validation_errors = []

    async def create_graph(self, message, validation_error=None):
        self.validation_errors.append(validation_error)
        if len(self.validation_errors) == 1:
            return PlannedTaskGraph(
                title="bad",
                goal="cycle",
                tasks=[
                    PlannedTask(
                        id="a",
                        description="a",
                        dependencies=["b"],
                        capability=TeamCapability.ANALYSIS,
                        success_criteria="done",
                    ),
                    PlannedTask(
                        id="b",
                        description="b",
                        dependencies=["a"],
                        capability=TeamCapability.ANALYSIS,
                        success_criteria="done",
                    ),
                ],
            )
        return valid_plan()


class CompletingOrchestrator:
    async def run(self, graph, attachments, emit):
        task = graph.tasks[0]
        task.status = TeamTaskStatus.RUNNING
        task.assigned_agent_id = "worker-1"
        task.attempt_count = 1
        await emit(
            TeamTaskEvent(
                graph_id=graph.id,
                task=task.model_copy(deep=True),
                agent_id="worker-1",
                attempt=1,
            ),
            True,
        )
        task.status = TeamTaskStatus.COMPLETED
        task.result = WorkerResult(success=True, summary="done")
        await emit(
            TeamTaskEvent(
                graph_id=graph.id,
                task=task.model_copy(deep=True),
                agent_id="worker-1",
                attempt=1,
            ),
            True,
        )
        finalize_graph(graph)
        return graph


class FakeSynthesizer:
    async def synthesize(self, graph):
        return FinalTeamResponse(message="final answer")


def test_team_flow_replans_once_and_emits_ordered_terminal_events():
    async def scenario():
        planner = ReplanningPlanner()
        uow = FakeUow()
        flow = TeamFlow(
            uow_factory=lambda: uow,
            session_id="session-1",
            team_max_tasks=5,
            planner=planner,
            orchestrator=CompletingOrchestrator(),
            synthesizer_factory=FakeSynthesizer,
        )

        events = [event async for event in flow.invoke(Message(message="research"))]

        assert planner.validation_errors[0] is None
        assert "cycle" in planner.validation_errors[1]
        assert [event.type for event in events] == [
            "title",
            "task_graph",
            "task",
            "task",
            "task_graph",
            "message",
            "done",
        ]
        assert isinstance(events[-2], MessageEvent)
        assert events[-2].message == "final answer"
        assert events[1].graph.status.value == "pending"
        assert events[-3].graph.status.value == "completed"
        assert flow.done

    asyncio.run(scenario())


class BlockingOrchestrator:
    def __init__(self):
        self.started = asyncio.Event()

    async def run(self, graph, attachments, emit):
        graph.tasks[0].status = TeamTaskStatus.RUNNING
        graph.tasks[0].assigned_agent_id = "worker-1"
        graph.tasks[0].attempt_count = 1
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_cancel_events_snapshot_active_tasks_and_graph():
    async def scenario():
        orchestrator = BlockingOrchestrator()
        uow = FakeUow()
        flow = TeamFlow(
            uow_factory=lambda: uow,
            session_id="session-1",
            team_max_tasks=5,
            planner=ReplanningPlanner(),
            orchestrator=orchestrator,
            synthesizer_factory=FakeSynthesizer,
        )

        async def consume():
            async for _ in flow.invoke(Message(message="research")):
                pass

        consumer = asyncio.create_task(consume())
        await orchestrator.started.wait()
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

        events = await flow.cancel_events()

        assert [event.type for event in events] == ["task", "task_graph"]
        assert events[0].task.status is TeamTaskStatus.CANCELLED
        assert events[0].task.error == "cancelled_by_user"
        assert events[1].graph.status.value == "cancelled"
        assert flow.done

    asyncio.run(scenario())


def test_flow_router_keeps_react_default_and_builds_team_lazily():
    react = object()
    team = object()
    calls = 0

    def team_factory():
        nonlocal calls
        calls += 1
        return team

    router = FlowRouter(react_flow=react, team_flow_factory=team_factory)

    assert router.resolve(AgentMode.REACT) is react
    assert calls == 0
    assert router.resolve(AgentMode.TEAM) is team
    assert calls == 1
