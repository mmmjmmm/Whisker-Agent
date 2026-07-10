import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.domain.external.llm import LLMErrorKind, LLMInvocationError
from app.domain.models.agent_run import (
    AgentRun,
    AgentTask,
    AttemptStatus,
    RunStatus,
    TaskAttempt,
    TaskStatus,
)
from app.domain.models.event import ResearchSourceEvent, ResearchTaskEvent
from app.domain.models.research import (
    NormalizedFinding,
    OrchestrationResult,
    ResearchPlan,
    WorkerContext,
)
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.research_worker import ResearchWorker
from app.domain.services.research.errors import (
    ResearchErrorCode,
    ResearchExecutionError,
)
from app.domain.services.research.event_sequencer import EventSequencer
from app.domain.services.research.evidence_normalizer import EvidenceNormalizer
from app.domain.services.research.memory_store import EphemeralMemoryStore
from app.domain.services.research.task_graph import TaskGraph


class ResearchWorkerFactory(Protocol):
    def create(
            self,
            *,
            task: AgentTask,
            attempt: TaskAttempt,
            memory_store: EphemeralMemoryStore,
            emit,
    ) -> ResearchWorker: ...


@dataclass
class TaskOutcome:
    status: TaskStatus
    finding: NormalizedFinding | None = None
    error: ResearchExecutionError | None = None


class ResearchOrchestrator:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            worker_factory: ResearchWorkerFactory,
            normalizer: EvidenceNormalizer,
            event_sequencer: EventSequencer,
            task_timeout_seconds: float | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._worker_factory = worker_factory
        self._normalizer = normalizer
        self._events = event_sequencer
        self._task_timeout_seconds = task_timeout_seconds
        self._cancel_event = asyncio.Event()
        self._normalization_lock = asyncio.Lock()

    def cancel(self) -> None:
        self._cancel_event.set()

    async def execute(
            self,
            plan: ResearchPlan,
            run: AgentRun,
            attachment_evidence_ids: list[str] | None = None,
    ) -> OrchestrationResult:
        graph = TaskGraph.build(plan.tasks, run.budget_snapshot)
        tasks_by_key = self._build_tasks(plan, run)
        await self._add_tasks(list(tasks_by_key.values()))
        semaphore = asyncio.Semaphore(run.budget_snapshot.max_workers)
        findings_by_key: dict[str, NormalizedFinding] = {}
        errors: dict[str, str] = {}
        running: dict[asyncio.Task[TaskOutcome], str] = {}
        cancelled = False

        while not graph.terminal():
            if self._cancel_event.is_set():
                cancelled = True
                break

            for spec in graph.ready_tasks():
                if any(key == spec.key for key in running.values()):
                    continue
                graph.start(spec.key)
                task = tasks_by_key[spec.key]
                task.status = TaskStatus.RUNNING
                await self._update_task(task)
                await self._publish_task(task)
                running[asyncio.create_task(self._execute_task(
                    task=task,
                    run=run,
                    tasks_by_key=tasks_by_key,
                    findings_by_key=findings_by_key,
                    semaphore=semaphore,
                    attachment_evidence_ids=attachment_evidence_ids or [],
                ))] = spec.key

            if not running:
                break

            cancel_waiter = asyncio.create_task(self._cancel_event.wait())
            done, _ = await asyncio.wait(
                [*running.keys(), cancel_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_waiter in done:
                cancelled = True
                for future in running:
                    future.cancel()
                await asyncio.gather(*running, return_exceptions=True)
                running.clear()
                break

            cancel_waiter.cancel()
            await asyncio.gather(cancel_waiter, return_exceptions=True)
            completed = [future for future in done if future in running]
            for future in completed:
                key = running.pop(future)
                outcome = future.result()
                task = tasks_by_key[key]
                if outcome.status == TaskStatus.COMPLETED:
                    graph.complete(key)
                    task.status = TaskStatus.COMPLETED
                    task.result_summary = outcome.finding.summary if outcome.finding else ""
                    if outcome.finding is not None:
                        findings_by_key[key] = outcome.finding
                    await self._update_task(task)
                    if outcome.finding is not None:
                        for source in outcome.finding.sources:
                            await self._events.publish(ResearchSourceEvent(
                                session_id=run.session_id,
                                run_id=run.id,
                                task_id=task.id,
                                agent_id=task.assigned_agent_id,
                                source=source,
                            ))
                    await self._publish_task(task)
                    continue

                skipped_keys = graph.terminate(key, outcome.status)
                task.status = outcome.status
                if outcome.error is not None:
                    task.error = {
                        "type": outcome.error.code.value,
                        "message": str(outcome.error),
                    }
                    errors[key] = str(outcome.error)
                await self._update_task(task)
                await self._publish_task(task)
                for skipped_key in skipped_keys:
                    skipped_task = tasks_by_key[skipped_key]
                    skipped_task.status = TaskStatus.SKIPPED
                    skipped_task.error = {
                        "type": "DependencyFailed",
                        "message": f"dependency failed for task {skipped_key}",
                    }
                    await self._update_task(skipped_task)
                    await self._publish_task(skipped_task)

        if cancelled:
            await self._cancel_non_terminal(tasks_by_key)
            run_status = RunStatus.CANCELLED
        else:
            statuses = [task.status for task in tasks_by_key.values()]
            failed = any(
                status in {
                    TaskStatus.FAILED,
                    TaskStatus.SKIPPED,
                    TaskStatus.TIMED_OUT,
                    TaskStatus.INTERRUPTED,
                }
                for status in statuses
            )
            completed = any(status == TaskStatus.COMPLETED for status in statuses)
            run_status = (
                RunStatus.PARTIAL
                if failed and completed
                else RunStatus.FAILED
                if failed
                else RunStatus.COMPLETED
            )

        return OrchestrationResult(
            run_status=run_status,
            status_by_key={
                key: task.status for key, task in tasks_by_key.items()
            },
            findings=list(findings_by_key.values()),
            errors=errors,
        )

    async def _execute_task(
            self,
            *,
            task: AgentTask,
            run: AgentRun,
            tasks_by_key: dict[str, AgentTask],
            findings_by_key: dict[str, NormalizedFinding],
            semaphore: asyncio.Semaphore,
            attachment_evidence_ids: list[str],
    ) -> TaskOutcome:
        max_attempts = run.budget_snapshot.max_attempts_per_task
        timeout = self._task_timeout_seconds or run.budget_snapshot.task_timeout_seconds

        async with semaphore:
            for attempt_number in range(1, max_attempts + 1):
                agent_id = str(uuid.uuid4())
                attempt = TaskAttempt(
                    run_id=run.id,
                    task_id=task.id,
                    attempt_number=attempt_number,
                    agent_id=agent_id,
                    agent_profile="worker",
                    model_profile="default",
                    status=AttemptStatus.RUNNING,
                    started_at=datetime.now(timezone.utc),
                )
                task.attempt_count = attempt_number
                task.assigned_agent_id = agent_id
                await self._update_task(task)
                await self._add_attempt(attempt)
                memory_store = EphemeralMemoryStore()
                worker = self._worker_factory.create(
                    task=task,
                    attempt=attempt,
                    memory_store=memory_store,
                    emit=self._events.publish,
                )
                context = self._worker_context(
                    task,
                    run,
                    tasks_by_key,
                    findings_by_key,
                    attachment_evidence_ids,
                    max_attempts - attempt_number,
                )

                try:
                    async with asyncio.timeout(timeout):
                        bundle = await worker.execute(context)
                        async with self._normalization_lock:
                            finding = await self._normalizer.normalize(run.id, bundle)
                except asyncio.CancelledError:
                    attempt.status = AttemptStatus.CANCELLED
                    attempt.finished_at = datetime.now(timezone.utc)
                    await self._update_attempt(attempt)
                    raise
                except Exception as exc:
                    error = self._classify_error(exc)
                    attempt.status = (
                        AttemptStatus.TIMED_OUT
                        if error.code == ResearchErrorCode.TASK_TIMEOUT
                        else AttemptStatus.FAILED
                    )
                    attempt.error_type = error.code.value
                    attempt.error_message = str(error)
                    attempt.finished_at = datetime.now(timezone.utc)
                    await self._update_attempt(attempt)
                    if error.retryable and attempt_number < max_attempts:
                        continue
                    task_status = (
                        TaskStatus.TIMED_OUT
                        if error.code == ResearchErrorCode.TASK_TIMEOUT
                        else TaskStatus.FAILED
                    )
                    return TaskOutcome(status=task_status, error=error)

                attempt.status = AttemptStatus.COMPLETED
                attempt.finished_at = datetime.now(timezone.utc)
                await self._update_attempt(attempt)
                return TaskOutcome(
                    status=TaskStatus.COMPLETED,
                    finding=finding,
                )

        raise RuntimeError("task attempt loop ended unexpectedly")

    @staticmethod
    def _build_tasks(
            plan: ResearchPlan,
            run: AgentRun,
    ) -> dict[str, AgentTask]:
        ids = {spec.key: str(uuid.uuid4()) for spec in plan.tasks}
        plan_version = max(1, run.plan_version)
        return {
            spec.key: AgentTask(
                id=ids[spec.key],
                run_id=run.id,
                plan_version=plan_version,
                task_key=spec.key,
                description=spec.description,
                objective=spec.objective,
                capability_profile=spec.capability_profile,
                dependency_ids=[ids[key] for key in spec.dependencies],
                acceptance_criteria=spec.acceptance_criteria,
                source_requirements=spec.source_requirements,
                required=spec.required,
                priority=spec.priority,
            )
            for spec in plan.tasks
        }

    @staticmethod
    def _worker_context(
            task: AgentTask,
            run: AgentRun,
            tasks_by_key: dict[str, AgentTask],
            findings_by_key: dict[str, NormalizedFinding],
            attachment_evidence_ids: list[str],
            remaining_attempts: int,
    ) -> WorkerContext:
        tasks_by_id = {item.id: item for item in tasks_by_key.values()}
        dependency_tasks = [tasks_by_id[item] for item in task.dependency_ids]
        dependency_keys = {item.task_key for item in dependency_tasks}
        evidence_ids = list(attachment_evidence_ids)
        for key in dependency_keys:
            finding = findings_by_key.get(key)
            if finding is not None:
                evidence_ids.extend(item.id for item in finding.evidence)
        return WorkerContext(
            run_id=run.id,
            goal=run.goal,
            task=task,
            dependency_summaries=[
                item.result_summary or "" for item in dependency_tasks
            ],
            evidence_ids=evidence_ids,
            remaining_attempts=remaining_attempts,
        )

    @staticmethod
    def _classify_error(exc: Exception) -> ResearchExecutionError:
        if isinstance(exc, ResearchExecutionError):
            return exc
        if isinstance(exc, TimeoutError):
            return ResearchExecutionError(
                code=ResearchErrorCode.TASK_TIMEOUT,
                message="research task timed out",
                retryable=True,
                scope="task",
            )
        if isinstance(exc, LLMInvocationError):
            code = {
                LLMErrorKind.RATE_LIMITED: ResearchErrorCode.MODEL_RATE_LIMITED,
                LLMErrorKind.TIMEOUT: ResearchErrorCode.MODEL_TIMEOUT,
            }.get(exc.kind, ResearchErrorCode.INFRASTRUCTURE_UNAVAILABLE)
            return ResearchExecutionError(
                code=code,
                message=str(exc),
                retryable=exc.retryable,
                scope="task",
            )
        return ResearchExecutionError(
            code=ResearchErrorCode.INFRASTRUCTURE_UNAVAILABLE,
            message=str(exc),
            retryable=False,
            scope="task",
        )

    async def _add_tasks(self, tasks: list[AgentTask]) -> None:
        async with self._uow_factory() as uow:
            await uow.agent_run.add_tasks(tasks)

    async def _update_task(self, task: AgentTask) -> None:
        async with self._uow_factory() as uow:
            await uow.agent_run.update_task(task)

    async def _add_attempt(self, attempt: TaskAttempt) -> None:
        async with self._uow_factory() as uow:
            await uow.agent_run.add_attempt(attempt)

    async def _update_attempt(self, attempt: TaskAttempt) -> None:
        async with self._uow_factory() as uow:
            await uow.agent_run.update_attempt(attempt)

    async def _publish_task(self, task: AgentTask) -> None:
        await self._events.publish(ResearchTaskEvent(
            task_id=task.id,
            agent_id=task.assigned_agent_id,
            task=task.model_copy(deep=True),
            status=task.status,
        ))

    async def _cancel_non_terminal(
            self,
            tasks_by_key: dict[str, AgentTask],
    ) -> None:
        terminal = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.SKIPPED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
            TaskStatus.INTERRUPTED,
        }
        for task in tasks_by_key.values():
            if task.status in terminal:
                continue
            task.status = TaskStatus.CANCELLED
            task.error = {"type": "RunCancelled", "message": "run cancelled"}
            await self._update_task(task)
            await self._publish_task(task)
