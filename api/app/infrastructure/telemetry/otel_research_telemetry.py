from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Status, StatusCode, Tracer

from app.domain.services.research.telemetry import TelemetryAttributes


class OTelResearchTelemetry:
    """OpenTelemetry adapter that deliberately excludes research content."""

    def __init__(
        self,
        *,
        tracer: Tracer | None = None,
        meter: Meter | None = None,
    ) -> None:
        self._tracer = tracer or trace.get_tracer(
            "mooc_manus.research_team"
        )
        self._meter = meter or metrics.get_meter(
            "mooc_manus.research_team"
        )
        self._run_duration = self._meter.create_histogram(
            "research.run.duration",
            unit="ms",
            description="Research run duration",
        )
        self._run_terminal = self._meter.create_counter(
            "research.run.terminal",
            unit="{run}",
            description="Research run terminal outcomes",
        )
        self._task_duration = self._meter.create_histogram(
            "research.task.duration",
            unit="ms",
            description="Research task duration",
        )
        self._llm_tokens = self._meter.create_counter(
            "research.llm.tokens",
            unit="{token}",
            description="Research LLM token usage",
        )
        self._tool_calls = self._meter.create_counter(
            "research.tool.calls",
            unit="{call}",
            description="Research tool calls",
        )
        self._tool_duration = self._meter.create_histogram(
            "research.tool.duration",
            unit="ms",
            description="Research tool call duration",
        )
        self._citation_coverage = self._meter.create_histogram(
            "research.citation.coverage",
            unit="1",
            description="Important claim citation coverage",
        )
        self._unsupported_claim_rate = self._meter.create_histogram(
            "research.claim.unsupported_rate",
            unit="1",
            description="Unsupported important claim rate",
        )
        self._independent_domains = self._meter.create_histogram(
            "research.source.independent_domains",
            unit="{domain}",
            description="Independent source domains per run",
        )

    @contextmanager
    def _span(
        self,
        name: str,
        attributes: dict[str, str],
    ) -> Iterator[None]:
        with self._tracer.start_as_current_span(
            name,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield
            except BaseException as error:
                span.set_attribute("error.type", type(error).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise

    @contextmanager
    def workflow_span(
        self,
        *,
        run_id: str,
        session_id: str,
        mode: str,
    ) -> Iterator[None]:
        metadata = TelemetryAttributes(
            run_id=run_id,
            session_id=session_id,
            mode=mode,
        )
        with self._span(
            "invoke_workflow ResearchTeamFlow",
            {
                "gen_ai.operation.name": "invoke_workflow",
                "gen_ai.workflow.name": "ResearchTeamFlow",
                "gen_ai.conversation.id": metadata.session_id or "",
                "agent.run.id": metadata.run_id,
                "agent.mode": metadata.mode or "research_team",
            },
        ):
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
        metadata = TelemetryAttributes(
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            agent_id=agent_id,
            agent_profile=agent_profile,
            model=model,
        )
        attributes = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.id": metadata.agent_id or "",
            "gen_ai.agent.name": metadata.agent_profile or "",
            "gen_ai.request.model": metadata.model or "",
            "agent.run.id": metadata.run_id,
        }
        if metadata.task_id:
            attributes["agent.task.id"] = metadata.task_id
        if metadata.attempt_id:
            attributes["agent.attempt.id"] = metadata.attempt_id
        with self._span(
            f"invoke_agent {metadata.agent_profile}",
            attributes,
        ):
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
        metadata = TelemetryAttributes(
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            tool_name=tool_name,
        )
        with self._span(
            f"execute_tool {metadata.tool_name}",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": metadata.tool_name or "",
                "agent.run.id": metadata.run_id,
                "agent.task.id": metadata.task_id or "",
                "agent.attempt.id": metadata.attempt_id or "",
            },
        ):
            yield

    def record_run_finished(self, *, status: str, elapsed_ms: int) -> None:
        attributes = {"research.run.status": status}
        self._run_duration.record(elapsed_ms, attributes)
        self._run_terminal.add(1, attributes)

    def record_task_finished(self, *, status: str, elapsed_ms: int) -> None:
        self._task_duration.record(
            elapsed_ms,
            {"research.task.status": status},
        )

    def record_llm_usage(
        self,
        *,
        agent_profile: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        base = {"research.agent.profile": agent_profile}
        self._llm_tokens.add(
            input_tokens,
            {**base, "gen_ai.token.type": "input"},
        )
        self._llm_tokens.add(
            output_tokens,
            {**base, "gen_ai.token.type": "output"},
        )

    def record_tool_call(
        self,
        *,
        tool_name: str,
        status: str,
        elapsed_ms: int,
    ) -> None:
        attributes = {
            "gen_ai.tool.name": tool_name,
            "research.tool.status": status,
        }
        self._tool_calls.add(1, attributes)
        self._tool_duration.record(elapsed_ms, attributes)

    def record_research_quality(
        self,
        *,
        citation_coverage: float,
        unsupported_claim_rate: float,
        independent_domains: int,
    ) -> None:
        self._citation_coverage.record(citation_coverage)
        self._unsupported_claim_rate.record(unsupported_claim_rate)
        self._independent_domains.record(independent_domains)
