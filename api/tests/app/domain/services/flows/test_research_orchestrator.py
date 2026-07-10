import asyncio

from app.domain.models.agent_run import (
    AgentMode,
    AgentRun,
    CapabilityProfile,
    RunBudget,
    RunStatus,
    TaskStatus,
)
from app.domain.models.event import DoneEvent
from app.domain.models.research import (
    FindingBundle,
    NormalizedFinding,
    ResearchPlan,
    ResearchTaskSpec,
)
from app.domain.services.flows.research_orchestrator import ResearchOrchestrator
from app.domain.services.research.budget import BudgetExceeded
from app.domain.services.research.errors import (
    ResearchErrorCode,
    ResearchExecutionError,
)
from app.domain.services.research.event_sequencer import EventSequencer


class Probe:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.overlap_count = 0

    async def enter(self) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active >= 2:
            self.overlap_count += 1

    async def exit(self) -> None:
        self.active -= 1


class FakeWorker:
    def __init__(self, key: str, probe: Probe, behavior: dict[str, list[object]]) -> None:
        self.key = key
        self.probe = probe
        self.behavior = behavior

    async def execute(self, context) -> FindingBundle:
        await self.probe.enter()
        try:
            await asyncio.sleep(0.01)
            outcomes = self.behavior.get(self.key)
            if outcomes:
                outcome = outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
            return FindingBundle(task_id=context.task.id, summary=f"done:{self.key}")
        finally:
            await self.probe.exit()


class FakeWorkerFactory:
    def __init__(self, probe: Probe, behavior=None) -> None:
        self.probe = probe
        self.behavior = behavior or {}
        self.memory_stores = []
        self.attempts = []

    def create(self, *, task, attempt, memory_store, emit):
        self.memory_stores.append(memory_store)
        self.attempts.append(attempt)
        return FakeWorker(task.task_key, self.probe, self.behavior)


class FakeNormalizer:
    async def normalize(self, run_id: str, bundle: FindingBundle) -> NormalizedFinding:
        return NormalizedFinding(
            task_id=bundle.task_id,
            summary=bundle.summary,
        )


class FakeRunRepository:
    def __init__(self) -> None:
        self.tasks = {}
        self.attempts = []

    async def add_tasks(self, tasks) -> None:
        self.tasks.update({task.id: task.model_copy(deep=True) for task in tasks})

    async def update_task(self, task) -> None:
        self.tasks[task.id] = task.model_copy(deep=True)

    async def add_attempt(self, attempt) -> None:
        self.attempts.append(attempt.model_copy(deep=True))

    async def update_attempt(self, attempt) -> None:
        for index, existing in enumerate(self.attempts):
            if existing.id == attempt.id:
                self.attempts[index] = attempt.model_copy(deep=True)
                return


class FakeUow:
    def __init__(self, repository) -> None:
        self.agent_run = repository

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


def task(key: str, dependencies=None) -> ResearchTaskSpec:
    return ResearchTaskSpec(
        key=key,
        description=key,
        objective=key,
        capability_profile=CapabilityProfile.RESEARCH_READONLY,
        dependencies=dependencies or [],
        acceptance_criteria=["evidence"],
    )


def run(max_workers: int = 4) -> AgentRun:
    return AgentRun(
        id="run-1",
        session_id="session-1",
        mode=AgentMode.RESEARCH_TEAM,
        goal="research",
        plan_version=1,
        budget_snapshot=RunBudget(max_workers=max_workers),
    )


async def collect(sequencer: EventSequencer, output: list) -> None:
    async for event in sequencer.events():
        output.append(event)


async def test_independent_tasks_overlap_and_respect_worker_limit() -> None:
    probe = Probe()
    factory = FakeWorkerFactory(probe)
    repository = FakeRunRepository()
    sequencer = EventSequencer("run-1")
    events = []
    collector = asyncio.create_task(collect(sequencer, events))
    orchestrator = ResearchOrchestrator(
        uow_factory=lambda: FakeUow(repository),
        worker_factory=factory,
        normalizer=FakeNormalizer(),
        event_sequencer=sequencer,
    )
    plan = ResearchPlan(
        title="parallel",
        goal="research",
        tasks=[task(f"task-{index}") for index in range(6)],
    )

    result = await orchestrator.execute(plan, run())
    await sequencer.close()
    await collector

    assert probe.max_active == 4
    assert probe.overlap_count >= 2
    assert result.run_status == RunStatus.COMPLETED
    assert all(status == TaskStatus.COMPLETED for status in result.status_by_key.values())
    assert [event.sequence_no for event in events] == list(range(1, len(events) + 1))
    assert all(event.run_id == "run-1" for event in events)
    assert not any(isinstance(event, DoneEvent) for event in events)


async def test_required_dependency_failure_skips_descendants() -> None:
    failure = ResearchExecutionError(
        code=ResearchErrorCode.TOOL_PERMANENT,
        message="failed",
        retryable=False,
        scope="task",
    )
    probe = Probe()
    factory = FakeWorkerFactory(probe, {"parent": [failure]})
    repository = FakeRunRepository()
    sequencer = EventSequencer("run-1")
    orchestrator = ResearchOrchestrator(
        uow_factory=lambda: FakeUow(repository),
        worker_factory=factory,
        normalizer=FakeNormalizer(),
        event_sequencer=sequencer,
    )
    plan = ResearchPlan(
        title="failure",
        goal="research",
        tasks=[task("parent"), task("child", ["parent"]), task("independent")],
    )

    result = await orchestrator.execute(plan, run())

    assert result.status_by_key["parent"] == TaskStatus.FAILED
    assert result.status_by_key["child"] == TaskStatus.SKIPPED
    assert result.status_by_key["independent"] == TaskStatus.COMPLETED
    assert result.run_status == RunStatus.PARTIAL


async def test_retryable_failure_uses_new_attempt_memory() -> None:
    transient = ResearchExecutionError(
        code=ResearchErrorCode.TOOL_TRANSIENT,
        message="retry",
        retryable=True,
        scope="task",
    )
    probe = Probe()
    factory = FakeWorkerFactory(probe, {"task": [transient, "success"]})
    repository = FakeRunRepository()
    orchestrator = ResearchOrchestrator(
        uow_factory=lambda: FakeUow(repository),
        worker_factory=factory,
        normalizer=FakeNormalizer(),
        event_sequencer=EventSequencer("run-1"),
    )
    plan = ResearchPlan(title="retry", goal="research", tasks=[task("task")])

    result = await orchestrator.execute(plan, run())

    assert result.status_by_key["task"] == TaskStatus.COMPLETED
    assert len(factory.attempts) == 2
    assert factory.memory_stores[0] is not factory.memory_stores[1]


async def test_task_timeout_retries_once_then_converges() -> None:
    probe = Probe()
    factory = FakeWorkerFactory(probe)
    repository = FakeRunRepository()
    orchestrator = ResearchOrchestrator(
        uow_factory=lambda: FakeUow(repository),
        worker_factory=factory,
        normalizer=FakeNormalizer(),
        event_sequencer=EventSequencer("run-1"),
        task_timeout_seconds=0.001,
    )
    plan = ResearchPlan(title="timeout", goal="research", tasks=[task("task")])

    result = await orchestrator.execute(plan, run())

    assert result.status_by_key["task"] == TaskStatus.TIMED_OUT
    assert result.run_status == RunStatus.FAILED
    assert len(factory.attempts) == 2


async def test_budget_exceeded_is_not_retried() -> None:
    probe = Probe()
    factory = FakeWorkerFactory(
        probe,
        {"task": [BudgetExceeded("max_llm_calls")]},
    )
    repository = FakeRunRepository()
    orchestrator = ResearchOrchestrator(
        uow_factory=lambda: FakeUow(repository),
        worker_factory=factory,
        normalizer=FakeNormalizer(),
        event_sequencer=EventSequencer("run-1"),
    )
    plan = ResearchPlan(title="budget", goal="research", tasks=[task("task")])

    result = await orchestrator.execute(plan, run())

    assert result.status_by_key["task"] == TaskStatus.FAILED
    assert len(factory.attempts) == 1


async def test_cancel_propagates_to_attempt_and_pending_tasks() -> None:
    class SlowWorker(FakeWorker):
        async def execute(self, context) -> FindingBundle:
            await self.probe.enter()
            try:
                await asyncio.sleep(10)
                return FindingBundle(task_id=context.task.id, summary="late")
            finally:
                await self.probe.exit()

    class SlowFactory(FakeWorkerFactory):
        def create(self, *, task, attempt, memory_store, emit):
            self.memory_stores.append(memory_store)
            self.attempts.append(attempt)
            return SlowWorker(task.task_key, self.probe, self.behavior)

    probe = Probe()
    factory = SlowFactory(probe)
    repository = FakeRunRepository()
    orchestrator = ResearchOrchestrator(
        uow_factory=lambda: FakeUow(repository),
        worker_factory=factory,
        normalizer=FakeNormalizer(),
        event_sequencer=EventSequencer("run-1"),
    )
    plan = ResearchPlan(
        title="cancel",
        goal="research",
        tasks=[task("running"), task("pending", ["running"])],
    )

    execution = asyncio.create_task(orchestrator.execute(plan, run()))
    while probe.active == 0:
        await asyncio.sleep(0)
    orchestrator.cancel()
    result = await execution

    assert result.run_status == RunStatus.CANCELLED
    assert result.status_by_key == {
        "running": TaskStatus.CANCELLED,
        "pending": TaskStatus.CANCELLED,
    }
    assert repository.attempts[0].status.value == "cancelled"
