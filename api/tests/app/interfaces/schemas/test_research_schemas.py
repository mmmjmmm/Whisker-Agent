import pytest
from pydantic import ValidationError

from app.application.errors.exceptions import (
    ResearchTeamDisabledError,
    RunAlreadyActiveError,
)
from app.domain.models.agent_run import AgentMode
from app.domain.models.research import ResearchSource
from app.interfaces.schemas.research import ResearchSourceResponse
from app.interfaces.schemas.session import ChatRequest


def test_chat_defaults_to_react() -> None:
    request = ChatRequest(message="hello")

    assert request.mode == AgentMode.REACT
    assert request.budget_profile == "default"


def test_chat_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="hello", mode="swarm")


def test_research_source_response_excludes_snapshot_location() -> None:
    source = ResearchSource(
        run_id="run-1",
        canonical_url="https://example.com/article",
        original_url="https://example.com/article?utm_source=test",
        title="Example",
        domain="example.com",
        content_type="text/html",
        content_hash="abc123",
        object_storage_key="research/run-1/abc123",
    )

    payload = ResearchSourceResponse.model_validate(source).model_dump()

    assert "object_storage_key" not in payload
    assert "raw_content" not in payload


def test_research_errors_have_stable_machine_codes() -> None:
    conflict = RunAlreadyActiveError("run-1", "running")
    disabled = ResearchTeamDisabledError()

    assert conflict.status_code == 409
    assert conflict.data == {
        "error_code": "RUN_ALREADY_ACTIVE",
        "run_id": "run-1",
        "status": "running",
    }
    assert disabled.status_code == 403
    assert disabled.data == {"error_code": "RESEARCH_TEAM_DISABLED"}
