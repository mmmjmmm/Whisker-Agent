import asyncio
from collections.abc import Awaitable, Callable
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
from app.domain.services.team.graph import (
    finalize_graph,
    propagate_skipped,
    ready_tasks,
)

EmitEvent = Callable[[BaseEvent, bool], Awaitable[None]]


class WorkerExecutor(Protocol):
    async def execute(
        self,
        *,
        goal: str,
        dependency_results: dict[str, WorkerResult],
        attachments: list[str],
        emit: EmitEvent,
    ) -> WorkerResult: ...


WorkerFactory = Callable[[str, str, TeamTask, int], WorkerExecutor]


class TeamOrchestrator:
    def __init__(
        self,
        *,
        worker_factory: WorkerFactory,
        is_parallel_safe: Callable[[TeamCapability], bool],
        max_workers: int,
        max_retries: int,
        timeout_seconds: float,
    ):
        self._worker_factory = worker_factory
        self._is_parallel_safe = is_parallel_safe
        self._max_workers = max_workers
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds

    async def _emit_task(
        self,
        graph: TaskGraph,
        task: TeamTask,
        emit: EmitEvent,
        wait_for_publish: bool = True,
    ) -> None:
        await emit(
            TeamTaskEvent(
                graph_id=graph.id,
                task=task.model_copy(deep=True),
                agent_id=task.assigned_agent_id,
                attempt=task.attempt_count,
            ),
            wait_for_publish,
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

        for attempt in range(1, self._max_retries + 2):
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
                    raise RuntimeError(result.summary or "worker reported failure")
                task.result = result
                task.status = TeamTaskStatus.COMPLETED
                await self._emit_task(graph, task, emit)
                return
            except asyncio.CancelledError:
                task.status = TeamTaskStatus.CANCELLED
                task.error = "cancelled"
                await self._emit_task(
                    graph,
                    task,
                    emit,
                    wait_for_publish=False,
                )
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
                return

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
