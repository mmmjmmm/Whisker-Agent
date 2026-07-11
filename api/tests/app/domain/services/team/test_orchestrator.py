import asyncio

import pytest

from app.domain.models.event import TeamTaskEvent
from app.domain.models.team import (
    PlannedTask,
    PlannedTaskGraph,
    TaskGraphStatus,
    TeamCapability,
    TeamTaskStatus,
    WorkerResult,
)
from app.domain.services.team.graph import build_task_graph
from app.domain.services.team.orchestrator import TeamOrchestrator


class Probe:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.exclusive_overlap = False


class ProbeWorker:
    def __init__(self, probe: Probe, capability: TeamCapability):
        self.probe = probe
        self.capability = capability

    async def execute(self, **kwargs):
        self.probe.active += 1
        self.probe.max_active = max(self.probe.max_active, self.probe.active)
        if self.capability is TeamCapability.BROWSER and self.probe.active != 1:
            self.probe.exclusive_overlap = True
        await asyncio.sleep(0.02)
        self.probe.active -= 1
        return WorkerResult(success=True, summary="done")


def task(task_id, capability, dependencies=None):
    return PlannedTask(
        id=task_id,
        description=task_id,
        dependencies=dependencies or [],
        capability=capability,
        success_criteria="done",
    )


async def record_event(events, event, wait_for_publish=True):
    events.append(event.model_copy(deep=True))


def test_parallel_safe_tasks_overlap_and_exclusive_task_does_not():
    async def scenario():
        probe = Probe()
        graph = build_task_graph(
            PlannedTaskGraph(
                title="t",
                goal="g",
                tasks=[
                    task("a", TeamCapability.SEARCH),
                    task("b", TeamCapability.FILE_READ),
                    task("c", TeamCapability.ANALYSIS),
                    task("d", TeamCapability.BROWSER),
                ],
            ),
            5,
        )
        events = []
        orchestrator = TeamOrchestrator(
            worker_factory=lambda graph_id, agent_id, planned, attempt: ProbeWorker(
                probe, planned.capability
            ),
            is_parallel_safe=lambda capability: capability
            in {
                TeamCapability.ANALYSIS,
                TeamCapability.SEARCH,
                TeamCapability.FILE_READ,
            },
            max_workers=3,
            max_retries=1,
            timeout_seconds=1,
        )

        result = await orchestrator.run(
            graph,
            [],
            lambda event, wait=True: record_event(events, event, wait),
        )

        assert result.status is TaskGraphStatus.COMPLETED
        assert probe.max_active == 3
        assert not probe.exclusive_overlap
        assert all(task.status is TeamTaskStatus.COMPLETED for task in graph.tasks)

    asyncio.run(scenario())


class AlwaysFailWorker:
    def __init__(self, calls: list[int], attempt: int):
        self.calls = calls
        self.attempt = attempt

    async def execute(self, **kwargs):
        self.calls.append(self.attempt)
        raise RuntimeError("boom")


def test_worker_retries_once_then_skips_dependents():
    async def scenario():
        calls: list[int] = []
        graph = build_task_graph(
            PlannedTaskGraph(
                title="retry",
                goal="retry once",
                tasks=[
                    task("failing", TeamCapability.SEARCH),
                    task(
                        "dependent",
                        TeamCapability.ANALYSIS,
                        dependencies=["failing"],
                    ),
                ],
            ),
            5,
        )
        events = []
        orchestrator = TeamOrchestrator(
            worker_factory=lambda graph_id, agent_id, planned, attempt: AlwaysFailWorker(
                calls, attempt
            ),
            is_parallel_safe=lambda capability: True,
            max_workers=3,
            max_retries=1,
            timeout_seconds=1,
        )

        await orchestrator.run(
            graph,
            [],
            lambda event, wait=True: record_event(events, event, wait),
        )

        assert calls == [1, 2]
        assert graph.task_by_id("failing").attempt_count == 2
        assert graph.task_by_id("failing").status is TeamTaskStatus.FAILED
        assert graph.task_by_id("dependent").status is TeamTaskStatus.SKIPPED
        assert graph.task_by_id("dependent").error == "dependency_failed"
        assert graph.status is TaskGraphStatus.FAILED
        assert [
            event.task.status
            for event in events
            if isinstance(event, TeamTaskEvent) and event.task.id == "failing"
        ] == [
            TeamTaskStatus.RUNNING,
            TeamTaskStatus.RETRYING,
            TeamTaskStatus.RUNNING,
            TeamTaskStatus.FAILED,
        ]

    asyncio.run(scenario())


class BlockingWorker:
    def __init__(self, started: asyncio.Event):
        self.started = started

    async def execute(self, **kwargs):
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_cancellation_marks_running_task_cancelled():
    async def scenario():
        started = asyncio.Event()
        graph = build_task_graph(
            PlannedTaskGraph(
                title="cancel",
                goal="cancel",
                tasks=[task("blocking", TeamCapability.SEARCH)],
            ),
            5,
        )
        events = []
        orchestrator = TeamOrchestrator(
            worker_factory=lambda graph_id, agent_id, planned, attempt: BlockingWorker(
                started
            ),
            is_parallel_safe=lambda capability: True,
            max_workers=3,
            max_retries=1,
            timeout_seconds=60,
        )
        running = asyncio.create_task(
            orchestrator.run(
                graph,
                [],
                lambda event, wait=True: record_event(events, event, wait),
            )
        )
        await started.wait()

        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        assert graph.task_by_id("blocking").status is TeamTaskStatus.CANCELLED
        assert any(
            isinstance(event, TeamTaskEvent)
            and event.task.id == "blocking"
            and event.task.status is TeamTaskStatus.CANCELLED
            for event in events
        )

    asyncio.run(scenario())
