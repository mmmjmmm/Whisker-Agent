import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentMode(str, Enum):
    REACT = "react"
    RESEARCH_TEAM = "research_team"


class RunStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    REVIEWING = "reviewing"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


class AttemptStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


class CapabilityProfile(str, Enum):
    RESEARCH_READONLY = "research_readonly"
    ANALYSIS = "analysis"


class RunBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_workers: int = Field(default=4, ge=1, le=5)
    max_tasks: int = Field(default=8, ge=1, le=20)
    max_graph_depth: int = Field(default=3, ge=1, le=6)
    max_research_waves: int = Field(default=2, ge=1, le=2)
    max_attempts_per_task: int = Field(default=2, ge=1, le=3)
    task_timeout_seconds: int = Field(default=180, ge=10, le=600)
    run_timeout_seconds: int = Field(default=900, ge=60, le=3600)
    max_llm_calls: int = Field(default=24, ge=1, le=100)
    max_tool_calls: int = Field(default=60, ge=1, le=300)
    max_total_tokens: int = Field(default=150_000, ge=1_000, le=1_000_000)


class RunUsage(BaseModel):
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    worker_attempts: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class AgentRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    mode: AgentMode
    status: RunStatus = RunStatus.PENDING
    goal: str
    plan_version: int = Field(default=0, ge=0)
    budget_snapshot: RunBudget = Field(default_factory=RunBudget)
    usage: RunUsage = Field(default_factory=RunUsage)
    error: dict[str, Any] | None = None
    heartbeat_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentTask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    plan_version: int = Field(ge=1)
    task_key: str
    description: str
    objective: str
    capability_profile: CapabilityProfile
    dependency_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    source_requirements: dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    priority: int = 0
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent_id: str | None = None
    result_summary: str | None = None
    error: dict[str, Any] | None = None
    attempt_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskAttempt(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    task_id: str
    attempt_number: int = Field(ge=1)
    agent_id: str
    agent_profile: str
    model_profile: str
    status: AttemptStatus = AttemptStatus.PENDING
    usage: RunUsage = Field(default_factory=RunUsage)
    error_type: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class InterruptedRun(BaseModel):
    run_id: str
    session_id: str

