from contextlib import contextmanager

from app.domain.services.research.telemetry import NoopResearchTelemetry


class RecordingResearchTelemetry(NoopResearchTelemetry):
    def __init__(self) -> None:
        self.workflow_spans: list[dict] = []
        self.agent_spans: list[dict] = []
        self.tool_spans: list[dict] = []
        self.run_results: list[dict] = []
        self.task_results: list[dict] = []
        self.llm_usage: list[dict] = []
        self.tool_calls: list[dict] = []
        self.quality: list[dict] = []
        self.worker_deltas: list[int] = []
        self.repair_waves = 0
        self.source_summaries: list[dict] = []
        self.retries: list[str] = []
        self.timeouts: list[str] = []
        self.budget_exhausted: list[str] = []

    @contextmanager
    def workflow_span(self, **metadata):
        self.workflow_spans.append(metadata)
        yield

    @contextmanager
    def agent_span(self, **metadata):
        self.agent_spans.append(metadata)
        yield

    @contextmanager
    def tool_span(self, **metadata):
        self.tool_spans.append(metadata)
        yield

    def record_run_finished(self, **result) -> None:
        self.run_results.append(result)

    def record_task_finished(self, **result) -> None:
        self.task_results.append(result)

    def record_llm_usage(self, **usage) -> None:
        self.llm_usage.append(usage)

    def record_tool_call(self, **result) -> None:
        self.tool_calls.append(result)

    def record_research_quality(self, **quality) -> None:
        self.quality.append(quality)

    def record_worker_active(self, *, delta: int) -> None:
        self.worker_deltas.append(delta)

    def record_repair_wave(self) -> None:
        self.repair_waves += 1

    def record_source_summary(
        self,
        *,
        source_count: int,
        independent_domains: int,
    ) -> None:
        self.source_summaries.append({
            "source_count": source_count,
            "independent_domains": independent_domains,
        })

    def record_retry(self, *, reason: str) -> None:
        self.retries.append(reason)

    def record_timeout(self, *, scope: str) -> None:
        self.timeouts.append(scope)

    def record_budget_exhausted(self, *, resource: str) -> None:
        self.budget_exhausted.append(resource)
