from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain.models.agent_run import (
    AgentMode,
    CapabilityProfile,
    RunBudget,
    RunStatus,
    RunUsage,
    TaskStatus,
)


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    mode: AgentMode
    status: RunStatus
    goal: str
    plan_version: int
    budget_snapshot: RunBudget
    usage: RunUsage
    error: dict[str, Any] | None
    heartbeat_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    plan_version: int
    task_key: str
    description: str
    objective: str
    capability_profile: CapabilityProfile
    dependency_ids: list[str]
    acceptance_criteria: list[str]
    source_requirements: dict[str, Any]
    required: bool
    priority: int
    status: TaskStatus
    assigned_agent_id: str | None
    result_summary: str | None
    error: dict[str, Any] | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime


class ResearchSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    canonical_url: str
    original_url: str
    title: str
    domain: str
    publisher: str | None
    published_at: datetime | None
    retrieved_at: datetime
    content_type: str
    content_hash: str
    source_class: str
    metadata: dict[str, Any]


class CancelRunResponse(BaseModel):
    run_id: str
    status: RunStatus
