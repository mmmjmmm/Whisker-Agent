import pytest
from pydantic import TypeAdapter, ValidationError

from app.domain.models.agent_run import AgentMode
from app.domain.models.run_command import (
    CancelRunCommand,
    RunCommand,
    StartRunCommand,
)


def test_start_command_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        StartRunCommand(
            session_id="session-1",
            run_id="run-1",
            mode="swarm",
            message="research",
        )


def test_run_command_discriminates_start_and_cancel() -> None:
    adapter = TypeAdapter(RunCommand)

    start = adapter.validate_python(
        {
            "command_type": "start",
            "session_id": "session-1",
            "run_id": "run-1",
            "mode": AgentMode.RESEARCH_TEAM,
            "message": "research",
        }
    )
    cancel = adapter.validate_python(
        {
            "command_type": "cancel",
            "session_id": "session-1",
            "run_id": "run-1",
        }
    )

    assert isinstance(start, StartRunCommand)
    assert isinstance(cancel, CancelRunCommand)
