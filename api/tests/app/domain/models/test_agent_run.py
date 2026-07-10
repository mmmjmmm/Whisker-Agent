from datetime import timezone

import pytest
from pydantic import ValidationError

from app.domain.models.agent_run import (
    AgentMode,
    AgentRun,
    AttemptStatus,
    RunBudget,
    RunStatus,
    TaskStatus,
)


def test_budget_is_immutable_and_has_approved_defaults() -> None:
    budget = RunBudget()

    assert budget.max_workers == 4
    assert budget.max_tasks == 8
    assert budget.max_research_waves == 2

    with pytest.raises(ValidationError):
        budget.max_workers = 5


def test_terminal_statuses_are_explicit() -> None:
    assert RunStatus.INTERRUPTED.value == "interrupted"
    assert TaskStatus.INTERRUPTED.value == "interrupted"
    assert AttemptStatus.INTERRUPTED.value == "interrupted"
    assert AgentMode.RESEARCH_TEAM.value == "research_team"


def test_agent_run_uses_timezone_aware_heartbeat() -> None:
    run = AgentRun(
        session_id="session-1",
        mode=AgentMode.RESEARCH_TEAM,
        goal="research",
    )

    assert run.heartbeat_at.tzinfo == timezone.utc

