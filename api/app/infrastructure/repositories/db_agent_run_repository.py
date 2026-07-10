from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.agent_run import (
    AgentMode,
    AgentRun,
    AgentTask,
    InterruptedRun,
    RunStatus,
    TaskAttempt,
    utc_now,
)
from app.domain.repositories.agent_run_repository import AgentRunRepository
from app.infrastructure.models.agent_run import (
    AgentRunModel,
    AgentTaskAttemptModel,
    AgentTaskDependencyModel,
    AgentTaskModel,
)


ACTIVE_RUN_STATUSES = {
    RunStatus.PENDING.value,
    RunStatus.PLANNING.value,
    RunStatus.RUNNING.value,
    RunStatus.REVIEWING.value,
    RunStatus.SYNTHESIZING.value,
}


class DBAgentRunRepository(AgentRunRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def add(self, run: AgentRun) -> None:
        self.db_session.add(AgentRunModel.from_domain(run))

    async def get(self, run_id: str) -> AgentRun | None:
        result = await self.db_session.execute(
            select(AgentRunModel).where(AgentRunModel.id == run_id)
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def get_active_by_session(self, session_id: str) -> AgentRun | None:
        result = await self.db_session.execute(
            select(AgentRunModel).where(
                AgentRunModel.session_id == session_id,
                AgentRunModel.mode == AgentMode.RESEARCH_TEAM.value,
                AgentRunModel.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def update(self, run: AgentRun) -> None:
        result = await self.db_session.execute(
            select(AgentRunModel).where(AgentRunModel.id == run.id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise ValueError(f"agent run not found: {run.id}")
        record.update_from_domain(run)

    async def add_tasks(self, tasks: list[AgentTask]) -> None:
        self.db_session.add_all([AgentTaskModel.from_domain(task) for task in tasks])
        dependencies = [
            AgentTaskDependencyModel(
                task_id=task.id,
                depends_on_task_id=dependency_id,
            )
            for task in tasks
            for dependency_id in task.dependency_ids
        ]
        if dependencies:
            self.db_session.add_all(dependencies)

    async def list_tasks(self, run_id: str) -> list[AgentTask]:
        task_result = await self.db_session.execute(
            select(AgentTaskModel)
            .where(AgentTaskModel.run_id == run_id)
            .order_by(AgentTaskModel.priority.desc(), AgentTaskModel.task_key)
        )
        records = list(task_result.scalars().all())
        if not records:
            return []

        task_ids = [record.id for record in records]
        dependency_result = await self.db_session.execute(
            select(AgentTaskDependencyModel).where(
                AgentTaskDependencyModel.task_id.in_(task_ids)
            )
        )
        dependency_map: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
        for dependency in dependency_result.scalars().all():
            dependency_map[dependency.task_id].append(dependency.depends_on_task_id)
        return [
            record.to_domain(sorted(dependency_map[record.id]))
            for record in records
        ]

    async def update_task(self, task: AgentTask) -> None:
        result = await self.db_session.execute(
            select(AgentTaskModel).where(AgentTaskModel.id == task.id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise ValueError(f"agent task not found: {task.id}")
        record.update_from_domain(task)

    async def add_attempt(self, attempt: TaskAttempt) -> None:
        self.db_session.add(AgentTaskAttemptModel.from_domain(attempt))

    async def update_attempt(self, attempt: TaskAttempt) -> None:
        result = await self.db_session.execute(
            select(AgentTaskAttemptModel).where(
                AgentTaskAttemptModel.id == attempt.id
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise ValueError(f"task attempt not found: {attempt.id}")
        record.update_from_domain(attempt)

    async def mark_active_interrupted(self, reason: str) -> list[InterruptedRun]:
        result = await self.db_session.execute(
            select(AgentRunModel).where(AgentRunModel.status.in_(ACTIVE_RUN_STATUSES))
        )
        records = list(result.scalars().all())
        interrupted: list[InterruptedRun] = []
        now = utc_now()
        for record in records:
            record.status = RunStatus.INTERRUPTED.value
            record.error = {
                "type": "ProcessInterrupted",
                "message": reason,
            }
            record.finished_at = now
            record.updated_at = now
            interrupted.append(
                InterruptedRun(run_id=record.id, session_id=record.session_id)
            )
        return interrupted

