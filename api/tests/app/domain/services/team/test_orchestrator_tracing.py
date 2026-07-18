import asyncio
from dataclasses import dataclass, field

from app.domain.models.team import (
    TaskGraph,
    TeamCapability,
    TeamTask,
    TeamTaskStatus,
    WorkerResult,
)
from app.domain.models.trace import TraceSpan, TraceSpanStatus, TraceSpanType
from app.domain.services.team.orchestrator import TeamOrchestrator
from app.domain.services.tracing import TraceRecorder


@dataclass
class FakeTraceRepository:
    spans: dict[str, TraceSpan] = field(default_factory=dict)

    async def create_span(self, span: TraceSpan) -> None:
        self.spans[span.id] = span

    async def finish_span(self, span: TraceSpan) -> None:
        self.spans[span.id] = span


class FakeUnitOfWork:
    def __init__(self, repository: FakeTraceRepository) -> None:
        self.trace = repository

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class TracedWorker:
    def __init__(
        self,
        recorder: TraceRecorder,
        task_id: str,
        attempt: int,
        max_attempts: int,
        *,
        fail: bool,
    ) -> None:
        self._recorder = recorder
        self._task_id = task_id
        self._attempt = attempt
        self._max_attempts = max_attempts
        self._fail = fail

    async def execute(
        self,
        *,
        goal: str,
        dependency_results: dict[str, WorkerResult],
        attachments: list[str],
        emit,
    ) -> WorkerResult:
        async with self._recorder.span(
            span_type=TraceSpanType.AGENT,
            name="task_worker.execute",
            attributes={
                "task_id": self._task_id,
                "attempt": self._attempt,
                "max_attempts": self._max_attempts,
            },
        ):
            await asyncio.sleep(0)
            if self._fail:
                raise RuntimeError(f"attempt {self._attempt} failed")
            return WorkerResult(success=True, summary=f"{self._task_id} done")


def make_task(task_id: str) -> TeamTask:
    return TeamTask(
        id=task_id,
        description=f"execute {task_id}",
        capability=TeamCapability.ANALYSIS,
        success_criteria="done",
    )


def test_orchestrator_records_one_task_span_across_worker_attempts() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        recorder = TraceRecorder(
            lambda: FakeUnitOfWork(repository),
            session_id="session-1",
        )
        graph = TaskGraph(
            id="graph-1",
            title="Trace",
            goal="trace this",
            tasks=[make_task("task-1")],
        )

        def worker_factory(graph_id, agent_id, task, attempt, max_attempts):
            return TracedWorker(
                recorder,
                task.id,
                attempt,
                max_attempts,
                fail=attempt == 1,
            )

        orchestrator = TeamOrchestrator(
            worker_factory=worker_factory,
            is_parallel_safe=lambda capability: True,
            max_workers=2,
            max_retries=1,
            timeout_seconds=1,
            trace_recorder=recorder,
        )
        emitted = []

        async def emit(event) -> None:
            emitted.append(event)

        async with recorder.span(
            span_type=TraceSpanType.FLOW,
            name="team",
            trace_id="trace-1",
        ) as flow_scope:
            result = await orchestrator.run(
                graph,
                [],
                emit,
            )

        assert result.tasks[0].result.summary == "task-1 done"
        task_spans = [
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.TASK
        ]
        agent_spans = sorted(
            (
                span for span in repository.spans.values()
                if span.span_type is TraceSpanType.AGENT
            ),
            key=lambda span: span.attributes["attempt"],
        )
        assert len(task_spans) == 1
        task_span = task_spans[0]
        assert task_span.name == "team.task"
        assert task_span.parent_span_id == flow_scope.handle.id
        assert task_span.status is TraceSpanStatus.OK
        assert task_span.attributes["graph_id"] == "graph-1"
        assert task_span.attributes["task_id"] == "task-1"
        assert task_span.attributes["attempt_count"] == 2
        assert task_span.output["status"] == "completed"
        assert [span.status for span in agent_spans] == [
            TraceSpanStatus.ERROR,
            TraceSpanStatus.OK,
        ]
        assert all(span.parent_span_id == task_span.id for span in agent_spans)

    asyncio.run(scenario())


def test_orchestrator_keeps_parallel_task_spans_as_flow_siblings() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        recorder = TraceRecorder(
            lambda: FakeUnitOfWork(repository),
            session_id="session-1",
        )
        graph = TaskGraph(
            id="graph-1",
            title="Trace",
            goal="trace this",
            tasks=[make_task("task-1"), make_task("task-2")],
        )

        def worker_factory(graph_id, agent_id, task, attempt, max_attempts):
            return TracedWorker(
                recorder,
                task.id,
                attempt,
                max_attempts,
                fail=False,
            )

        orchestrator = TeamOrchestrator(
            worker_factory=worker_factory,
            is_parallel_safe=lambda capability: True,
            max_workers=2,
            max_retries=0,
            timeout_seconds=1,
            trace_recorder=recorder,
        )

        async with recorder.span(
            span_type=TraceSpanType.FLOW,
            name="team",
            trace_id="trace-1",
        ) as flow_scope:
            await orchestrator.run(graph, [], lambda event: asyncio.sleep(0))

        task_spans = {
            span.attributes["task_id"]: span
            for span in repository.spans.values()
            if span.span_type is TraceSpanType.TASK
        }
        agent_spans = [
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        ]
        assert set(task_spans) == {"task-1", "task-2"}
        assert all(
            span.parent_span_id == flow_scope.handle.id
            for span in task_spans.values()
        )
        assert all(
            span.parent_span_id == task_spans[span.attributes["task_id"]].id
            for span in agent_spans
        )

    asyncio.run(scenario())


def test_orchestrator_does_not_create_span_for_skipped_task() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        recorder = TraceRecorder(
            lambda: FakeUnitOfWork(repository),
            session_id="session-1",
        )
        graph = TaskGraph(
            id="graph-1",
            title="Trace",
            goal="trace this",
            tasks=[
                make_task("task-1"),
                TeamTask(
                    id="task-2",
                    description="depends on task-1",
                    dependencies=["task-1"],
                    capability=TeamCapability.ANALYSIS,
                    success_criteria="done",
                ),
            ],
        )

        def worker_factory(graph_id, agent_id, task, attempt, max_attempts):
            return TracedWorker(
                recorder,
                task.id,
                attempt,
                max_attempts,
                fail=True,
            )

        orchestrator = TeamOrchestrator(
            worker_factory=worker_factory,
            is_parallel_safe=lambda capability: True,
            max_workers=2,
            max_retries=0,
            timeout_seconds=1,
            trace_recorder=recorder,
        )

        async with recorder.span(
            span_type=TraceSpanType.FLOW,
            name="team",
            trace_id="trace-1",
        ):
            await orchestrator.run(graph, [], lambda event: asyncio.sleep(0))

        task_spans = [
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.TASK
        ]
        assert graph.task_by_id("task-2").status is TeamTaskStatus.SKIPPED
        assert [span.attributes["task_id"] for span in task_spans] == [
            "task-1"
        ]

    asyncio.run(scenario())
