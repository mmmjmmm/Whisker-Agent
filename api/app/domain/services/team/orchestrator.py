import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Protocol

from app.domain.models.event import BaseEvent, TeamTaskEvent
from app.domain.models.team import (
    TaskGraph,
    TaskGraphStatus,
    TeamCapability,
    TeamTask,
    TeamTaskStatus,
    WorkerResult,
)
from app.domain.models.trace import TraceSpanStatus, TraceSpanType
from app.domain.services.tracing import TraceRecorder
from app.domain.services.team.graph import (
    finalize_graph,
    propagate_skipped,
    ready_tasks,
)

EmitEvent = Callable[[BaseEvent], Awaitable[None]]


class WorkerExecutor(Protocol):
    async def execute(
        self,
        *,
        goal: str,
        dependency_results: dict[str, WorkerResult],
        attachments: list[str],
        emit: EmitEvent,
    ) -> WorkerResult: ...


WorkerFactory = Callable[[str, str, TeamTask, int, int], WorkerExecutor]


class TeamOrchestrator:
    def __init__(
        self,
        *,
        worker_factory: WorkerFactory,
        is_parallel_safe: Callable[[TeamCapability], bool],
        max_workers: int,
        max_retries: int,
        timeout_seconds: float,
        trace_recorder: TraceRecorder | None = None,
    ):
        self._worker_factory = worker_factory
        self._is_parallel_safe = is_parallel_safe
        self._max_workers = max_workers
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._trace_recorder = trace_recorder

    @asynccontextmanager
    async def _trace_task(self, graph: TaskGraph, task: TeamTask):
        if self._trace_recorder is None:
            yield None
            return

        async with self._trace_recorder.span(
            span_type=TraceSpanType.TASK,
            name="team.task",
            input=task.model_dump(
                mode="json",
                include={
                    "id",
                    "description",
                    "dependencies",
                    "capability",
                    "success_criteria",
                },
            ),
            attributes={
                "graph_id": graph.id,
                "task_id": task.id,
                "description": task.description,
                "capability": task.capability.value,
                "max_attempts": self._max_retries + 1,
            },
        ) as scope:
            yield scope

    async def _emit_task(
        self,
        graph: TaskGraph,
        task: TeamTask,
        emit: EmitEvent,
    ) -> None:
        await emit(
            TeamTaskEvent(
                graph_id=graph.id,
                task=task.model_copy(deep=True),
                agent_id=task.assigned_agent_id,
                attempt=task.attempt_count,
            )
        )

    async def _execute_task(
        self,
        graph: TaskGraph,
        task: TeamTask,
        slot: int,
        attachments: list[str],
        emit: EmitEvent,
    ) -> None:
        task.assigned_agent_id = f"worker-{slot}"
        dependency_results = {
            dependency: dependency_task.result
            for dependency in task.dependencies
            if (dependency_task := graph.task_by_id(dependency)).result is not None
        }

        max_attempts = self._max_retries + 1
        async with self._trace_task(graph, task) as trace_scope:
            try:
                for attempt in range(1, max_attempts + 1):
                    task.attempt_count = attempt
                    task.status = TeamTaskStatus.RUNNING
                    task.error = None
                    await self._emit_task(graph, task, emit)
                    try:
                        worker = self._worker_factory(
                            graph.id,
                            task.assigned_agent_id,
                            task,
                            attempt,
                            max_attempts,
                        )
                        result = await asyncio.wait_for(
                            worker.execute(
                                goal=graph.goal,
                                dependency_results=dependency_results,
                                attachments=attachments,
                                emit=emit,
                            ),
                            timeout=self._timeout_seconds,
                        )
                        if not result.success:
                            raise RuntimeError(
                                result.summary or "worker reported failure"
                            )
                        task.result = result
                        task.status = TeamTaskStatus.COMPLETED
                        await self._emit_task(graph, task, emit)
                        if trace_scope is not None:
                            trace_scope.finish(
                                output=task.model_dump(mode="json"),
                                attributes={
                                    "agent_id": task.assigned_agent_id,
                                    "attempt_count": task.attempt_count,
                                    "task_status": task.status.value,
                                },
                            )
                        return
                    except asyncio.CancelledError:
                        task.status = TeamTaskStatus.CANCELLED
                        task.error = "cancelled"
                        raise
                    except TimeoutError:
                        task.error = "task_timeout"
                    except Exception as exc:
                        task.error = str(exc)

                    if attempt <= self._max_retries:
                        task.status = TeamTaskStatus.RETRYING
                        await self._emit_task(graph, task, emit)
                    else:
                        task.status = TeamTaskStatus.FAILED
                        await self._emit_task(graph, task, emit)
                        if trace_scope is not None:
                            trace_scope.finish(
                                output=task.model_dump(mode="json"),
                                error={"message": task.error or "task failed"},
                                attributes={
                                    "agent_id": task.assigned_agent_id,
                                    "attempt_count": task.attempt_count,
                                    "task_status": task.status.value,
                                },
                            )
                        return
            except asyncio.CancelledError:
                if trace_scope is not None:
                    trace_scope.finish(
                        output=task.model_dump(mode="json"),
                        status=TraceSpanStatus.CANCELLED,
                        attributes={
                            "agent_id": task.assigned_agent_id,
                            "attempt_count": task.attempt_count,
                            "task_status": task.status.value,
                        },
                    )
                raise

    async def run(
        self,
        graph: TaskGraph,
        attachments: list[str],
        emit: EmitEvent,
    ) -> TaskGraph:
        graph.status = TaskGraphStatus.RUNNING
        terminal_statuses = {
            TeamTaskStatus.COMPLETED,
            TeamTaskStatus.FAILED,
            TeamTaskStatus.SKIPPED,
            TeamTaskStatus.CANCELLED,
        }

        while True:
            for skipped in propagate_skipped(graph):
                await self._emit_task(graph, skipped, emit)

            ready = ready_tasks(graph)
            if not ready:
                if all(task.status in terminal_statuses for task in graph.tasks):
                    break
                graph.status = TaskGraphStatus.FAILED
                graph.error = "scheduler_deadlock"
                break

            parallel_safe = [
                task for task in ready if self._is_parallel_safe(task.capability)
            ]
            if parallel_safe:
                batch = parallel_safe[: self._max_workers]
                await asyncio.gather(
                    *(
                        self._execute_task(
                            graph,
                            task,
                            slot + 1,
                            attachments,
                            emit,
                        )
                        for slot, task in enumerate(batch)
                    )
                )
                continue

            await self._execute_task(graph, ready[0], 1, attachments, emit)

        finalize_graph(graph)
        return graph
