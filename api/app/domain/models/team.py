import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class AgentMode(str, Enum):
    REACT = "react"
    TEAM = "team"


class TeamCapability(str, Enum):
    ANALYSIS = "analysis"
    SEARCH = "search"
    BROWSER = "browser"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    SHELL = "shell"
    MCP = "mcp"
    A2A = "a2a"


class TeamTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class TaskGraphStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: HttpUrl
    snippet: str | None = None


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    summary: str
    sources: list[SourceRef] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_success_summary(self):
        if self.success and not self.summary.strip():
            raise ValueError("successful worker result requires summary")
        return self


class FinalTeamResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    attachments: list[str] = Field(default_factory=list)


class PlannedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    capability: TeamCapability
    success_criteria: str = Field(min_length=1)


class PlannedTaskGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    tasks: list[PlannedTask]


class TeamTask(PlannedTask):
    status: TeamTaskStatus = TeamTaskStatus.PENDING
    assigned_agent_id: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    result: WorkerResult | None = None
    error: str | None = None


class TaskGraph(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    goal: str
    tasks: list[TeamTask]
    status: TaskGraphStatus = TaskGraphStatus.PENDING
    error: str | None = None

    def task_by_id(self, task_id: str) -> TeamTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)
