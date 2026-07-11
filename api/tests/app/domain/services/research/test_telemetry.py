import pytest
from pydantic import ValidationError

from app.domain.services.research.telemetry import (
    NoopResearchTelemetry,
    TelemetryAttributes,
)


def test_noop_telemetry_accepts_structured_metadata() -> None:
    telemetry = NoopResearchTelemetry()

    with telemetry.workflow_span(
        run_id="run-1",
        session_id="session-1",
        mode="research_team",
    ):
        with telemetry.agent_span(
            run_id="run-1",
            task_id="task-1",
            attempt_id="attempt-1",
            agent_id="worker-1",
            agent_profile="worker",
            model="test-model",
        ):
            with telemetry.tool_span(
                run_id="run-1",
                task_id="task-1",
                attempt_id="attempt-1",
                tool_name="web_read",
            ):
                pass

    telemetry.record_run_finished(status="partial", elapsed_ms=1200)
    telemetry.record_task_finished(status="timed_out", elapsed_ms=180000)
    telemetry.record_llm_usage(
        agent_profile="worker",
        input_tokens=100,
        output_tokens=50,
    )
    telemetry.record_tool_call(
        tool_name="web_read",
        status="failed",
        elapsed_ms=250,
    )
    telemetry.record_research_quality(
        citation_coverage=0.95,
        unsupported_claim_rate=0.02,
        independent_domains=5,
    )


@pytest.mark.parametrize(
    "field",
    [
        "prompt",
        "messages",
        "content",
        "excerpt",
        "function_args",
        "api_key",
    ],
)
def test_attributes_reject_content_fields(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        TelemetryAttributes(run_id="run-1", **{field: "secret"})
