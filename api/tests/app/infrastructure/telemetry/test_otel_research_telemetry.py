import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from app.infrastructure.telemetry.otel_research_telemetry import (
    OTelResearchTelemetry,
)


def _telemetry():
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    telemetry = OTelResearchTelemetry(
        tracer=tracer_provider.get_tracer("test"),
        meter=meter_provider.get_meter("test"),
    )
    return telemetry, span_exporter, metric_reader


def test_workflow_agent_and_tool_spans_are_correlated() -> None:
    telemetry, span_exporter, _ = _telemetry()

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

    spans = span_exporter.get_finished_spans()
    assert [span.name for span in spans] == [
        "execute_tool web_read",
        "invoke_agent worker",
        "invoke_workflow ResearchTeamFlow",
    ]
    assert all(span.attributes["agent.run.id"] == "run-1" for span in spans)
    assert spans[0].parent.span_id == spans[1].context.span_id
    assert spans[1].parent.span_id == spans[2].context.span_id
    serialized_attributes = " ".join(
        str(dict(span.attributes)) for span in spans
    )
    assert "prompt" not in serialized_attributes
    assert "function_args" not in serialized_attributes


def test_span_errors_record_type_without_exception_content() -> None:
    telemetry, span_exporter, _ = _telemetry()

    with pytest.raises(RuntimeError, match="private failure details"):
        with telemetry.tool_span(
            run_id="run-1",
            task_id="task-1",
            attempt_id="attempt-1",
            tool_name="web_read",
        ):
            raise RuntimeError("private failure details")

    span = span_exporter.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "RuntimeError"
    assert span.events == ()
    assert "private failure details" not in str(dict(span.attributes))


def test_metrics_use_fixed_names_and_content_free_attributes() -> None:
    telemetry, _, metric_reader = _telemetry()

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
    telemetry.record_worker_active(delta=1)
    telemetry.record_worker_active(delta=-1)
    telemetry.record_repair_wave()
    telemetry.record_source_summary(
        source_count=8,
        independent_domains=5,
    )
    telemetry.record_retry(reason="ToolTransientError")
    telemetry.record_timeout(scope="task")
    telemetry.record_budget_exhausted(resource="max_llm_calls")

    metrics = metric_reader.get_metrics_data()
    names = {
        metric.name
        for resource_metric in metrics.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
    }
    assert names >= {
        "research.run.duration",
        "research.run.terminal",
        "research.task.duration",
        "research.llm.tokens",
        "research.tool.calls",
        "research.tool.duration",
        "research.citation.coverage",
        "research.claim.unsupported_rate",
        "research.source.independent_domains",
        "research.worker.active",
        "research.repair.wave",
        "research.source.count",
        "research.retry",
        "research.timeout",
        "research.budget.exhausted",
    }
