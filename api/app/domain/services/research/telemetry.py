from collections.abc import Iterator
from contextlib import contextmanager
from typing import ContextManager, Protocol

from pydantic import BaseModel, ConfigDict


class TelemetryAttributes(BaseModel):
    """Strict metadata allowlist; research content must never enter telemetry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str | None = None
    run_id: str
    task_id: str | None = None
    attempt_id: str | None = None
    agent_id: str | None = None
    agent_profile: str | None = None
    model: str | None = None
    mode: str | None = None
    tool_name: str | None = None
    status: str | None = None


class ResearchTelemetry(Protocol):
    def workflow_span(
        self,
        *,
        run_id: str,
        session_id: str,
        mode: str,
    ) -> ContextManager[None]: ...

    def agent_span(
        self,
        *,
        run_id: str,
        task_id: str | None,
        attempt_id: str | None,
        agent_id: str,
        agent_profile: str,
        model: str,
    ) -> ContextManager[None]: ...

    def tool_span(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt_id: str,
        tool_name: str,
    ) -> ContextManager[None]: ...

    def record_run_finished(self, *, status: str, elapsed_ms: int) -> None: ...

    def record_task_finished(self, *, status: str, elapsed_ms: int) -> None: ...

    def record_llm_usage(
        self,
        *,
        agent_profile: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None: ...

    def record_tool_call(
        self,
        *,
        tool_name: str,
        status: str,
        elapsed_ms: int,
    ) -> None: ...

    def record_research_quality(
        self,
        *,
        citation_coverage: float,
        unsupported_claim_rate: float,
        independent_domains: int,
    ) -> None: ...


class NoopResearchTelemetry:
    @contextmanager
    def workflow_span(
        self,
        *,
        run_id: str,
        session_id: str,
        mode: str,
    ) -> Iterator[None]:
        TelemetryAttributes(run_id=run_id, session_id=session_id, mode=mode)
        yield

    @contextmanager
    def agent_span(
        self,
        *,
        run_id: str,
        task_id: str | None,
        attempt_id: str | None,
        agent_id: str,
        agent_profile: str,
        model: str,
    ) -> Iterator[None]:
        TelemetryAttributes(
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            agent_id=agent_id,
            agent_profile=agent_profile,
            model=model,
        )
        yield

    @contextmanager
    def tool_span(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt_id: str,
        tool_name: str,
    ) -> Iterator[None]:
        TelemetryAttributes(
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            tool_name=tool_name,
        )
        yield

    def record_run_finished(self, *, status: str, elapsed_ms: int) -> None:
        return None

    def record_task_finished(self, *, status: str, elapsed_ms: int) -> None:
        return None

    def record_llm_usage(
        self,
        *,
        agent_profile: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        return None

    def record_tool_call(
        self,
        *,
        tool_name: str,
        status: str,
        elapsed_ms: int,
    ) -> None:
        return None

    def record_research_quality(
        self,
        *,
        citation_coverage: float,
        unsupported_claim_rate: float,
        independent_domains: int,
    ) -> None:
        return None
