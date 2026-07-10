from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.agent_run import AgentRun, AgentTask, TaskAttempt
from app.infrastructure.models.base import Base


class AgentRunModel(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_runs_id"),
        Index(
            "uq_agent_runs_active_research_session",
            "session_id",
            unique=True,
            postgresql_where=text(
                "mode = 'research_team' AND status IN "
                "('pending','planning','running','reviewing','synthesizing')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    budget_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @classmethod
    def from_domain(cls, run: AgentRun) -> "AgentRunModel":
        return cls(
            id=run.id,
            session_id=run.session_id,
            mode=run.mode.value,
            status=run.status.value,
            goal=run.goal,
            plan_version=run.plan_version,
            budget_snapshot=run.budget_snapshot.model_dump(mode="json"),
            usage=run.usage.model_dump(mode="json"),
            error=run.error,
            heartbeat_at=run.heartbeat_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def to_domain(self) -> AgentRun:
        return AgentRun.model_validate({
            "id": self.id,
            "session_id": self.session_id,
            "mode": self.mode,
            "status": self.status,
            "goal": self.goal,
            "plan_version": self.plan_version,
            "budget_snapshot": self.budget_snapshot,
            "usage": self.usage,
            "error": self.error,
            "heartbeat_at": self.heartbeat_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })

    def update_from_domain(self, run: AgentRun) -> None:
        replacement = AgentRunModel.from_domain(run)
        for column in self.__table__.columns:
            if column.name != "id":
                setattr(self, column.name, getattr(replacement, column.name))


class AgentTaskModel(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_tasks_id"),
        UniqueConstraint(
            "run_id",
            "plan_version",
            "task_key",
            name="uq_agent_tasks_run_plan_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    task_key: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    capability_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    acceptance_criteria: Mapped[list] = mapped_column(JSONB, nullable=False)
    source_requirements: Mapped[dict] = mapped_column(JSONB, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @classmethod
    def from_domain(cls, task: AgentTask) -> "AgentTaskModel":
        return cls(
            id=task.id,
            run_id=task.run_id,
            plan_version=task.plan_version,
            task_key=task.task_key,
            description=task.description,
            objective=task.objective,
            capability_profile=task.capability_profile.value,
            acceptance_criteria=task.acceptance_criteria,
            source_requirements=task.source_requirements,
            required=task.required,
            priority=task.priority,
            status=task.status.value,
            assigned_agent_id=task.assigned_agent_id,
            result_summary=task.result_summary,
            error=task.error,
            attempt_count=task.attempt_count,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    def to_domain(self, dependency_ids: list[str] | None = None) -> AgentTask:
        return AgentTask.model_validate({
            "id": self.id,
            "run_id": self.run_id,
            "plan_version": self.plan_version,
            "task_key": self.task_key,
            "description": self.description,
            "objective": self.objective,
            "capability_profile": self.capability_profile,
            "dependency_ids": dependency_ids or [],
            "acceptance_criteria": self.acceptance_criteria,
            "source_requirements": self.source_requirements,
            "required": self.required,
            "priority": self.priority,
            "status": self.status,
            "assigned_agent_id": self.assigned_agent_id,
            "result_summary": self.result_summary,
            "error": self.error,
            "attempt_count": self.attempt_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })

    def update_from_domain(self, task: AgentTask) -> None:
        replacement = AgentTaskModel.from_domain(task)
        for column in self.__table__.columns:
            if column.name not in {"id", "run_id"}:
                setattr(self, column.name, getattr(replacement, column.name))


class AgentTaskDependencyModel(Base):
    __tablename__ = "agent_task_dependencies"
    __table_args__ = (
        PrimaryKeyConstraint(
            "task_id",
            "depends_on_task_id",
            name="pk_agent_task_dependencies",
        ),
    )

    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )


class AgentTaskAttemptModel(Base):
    __tablename__ = "agent_task_attempts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_agent_task_attempts_id"),
        UniqueConstraint(
            "run_id",
            "task_id",
            "attempt_number",
            name="uq_agent_task_attempts_run_task_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    model_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @classmethod
    def from_domain(cls, attempt: TaskAttempt) -> "AgentTaskAttemptModel":
        return cls(
            id=attempt.id,
            run_id=attempt.run_id,
            task_id=attempt.task_id,
            attempt_number=attempt.attempt_number,
            agent_id=attempt.agent_id,
            agent_profile=attempt.agent_profile,
            model_profile=attempt.model_profile,
            status=attempt.status.value,
            usage=attempt.usage.model_dump(mode="json"),
            error_type=attempt.error_type,
            error_message=attempt.error_message,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
        )

    def to_domain(self) -> TaskAttempt:
        return TaskAttempt.model_validate({
            "id": self.id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "attempt_number": self.attempt_number,
            "agent_id": self.agent_id,
            "agent_profile": self.agent_profile,
            "model_profile": self.model_profile,
            "status": self.status,
            "usage": self.usage,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        })

    def update_from_domain(self, attempt: TaskAttempt) -> None:
        replacement = AgentTaskAttemptModel.from_domain(attempt)
        for column in self.__table__.columns:
            if column.name not in {"id", "run_id", "task_id"}:
                setattr(self, column.name, getattr(replacement, column.name))
