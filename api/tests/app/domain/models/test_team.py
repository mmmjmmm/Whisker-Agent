import pytest
from pydantic import ValidationError

from app.domain.models.app_config import AgentConfig
from app.domain.models.team import (
    AgentMode,
    PlannedTask,
    PlannedTaskGraph,
    SourceRef,
    TeamCapability,
    WorkerResult,
)


def test_team_config_has_conservative_defaults():
    config = AgentConfig()

    assert config.team_max_tasks == 5
    assert config.team_max_workers == 3
    assert config.team_max_task_retries == 1
    assert config.team_task_timeout_seconds == 300
    assert config.team_max_worker_iterations == 20


def test_successful_worker_result_requires_summary():
    with pytest.raises(ValidationError, match="summary"):
        WorkerResult(success=True, summary="  ")


def test_source_ref_only_accepts_http_urls():
    with pytest.raises(ValidationError):
        SourceRef(title="local", url="file:///etc/passwd")


def test_planner_model_contains_no_runtime_fields():
    planned = PlannedTask.model_validate(
        {
            "id": "collect",
            "description": "收集资料",
            "dependencies": [],
            "capability": "search",
            "success_criteria": "至少返回一个来源",
        }
    )

    assert planned.capability is TeamCapability.SEARCH
    assert "status" not in PlannedTask.model_fields
    assert AgentMode.REACT.value == "react"
    assert PlannedTaskGraph(title="t", goal="g", tasks=[planned]).tasks == [planned]


def test_planner_model_rejects_runtime_fields_from_llm():
    with pytest.raises(ValidationError):
        PlannedTask.model_validate(
            {
                "id": "collect",
                "description": "收集资料",
                "dependencies": [],
                "capability": "search",
                "success_criteria": "完成",
                "status": "completed",
            }
        )
