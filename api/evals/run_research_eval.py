#!/usr/bin/env python
import argparse
import asyncio
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from app.domain.models.agent_run import AgentMode, RunUsage
from app.domain.models.research import (
    EvidenceExcerpt,
    ResearchClaim,
    ResearchSource,
)
from app.domain.services.research.evaluation import (
    EvalCase,
    ResearchEvaluationArtifact,
    ResearchQualityMetrics,
    evaluate_report,
    load_cases,
)


class JudgeScores(BaseModel):
    correctness: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    conflict_handling: float = Field(ge=0, le=1)
    clarity: float = Field(ge=0, le=1)

    def mean(self) -> float:
        return (
            self.correctness
            + self.coverage
            + self.conflict_handling
            + self.clarity
        ) / 4


class EvalArtifact(BaseModel):
    case_id: str
    mode: AgentMode
    model_profile: str
    final_report: str
    run_status: str
    claims: list[ResearchClaim] = Field(default_factory=list)
    evidence: list[EvidenceExcerpt] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    required_topics: list[str] = Field(default_factory=list)
    covered_topics: list[str] = Field(default_factory=list)
    judge_scores: JudgeScores | None = None
    usage: RunUsage = Field(default_factory=RunUsage)
    elapsed_ms: int = Field(ge=0)
    max_parallel_workers: int = Field(default=0, ge=0)
    policy_bypass_count: int = Field(default=0, ge=0)


class EvalExecutor(Protocol):
    async def execute(
        self,
        case: EvalCase,
        mode: AgentMode,
        model_profile: str,
    ) -> EvalArtifact: ...


class ModeComparison(BaseModel):
    baseline: list[EvalArtifact]
    candidate: list[EvalArtifact]
    baseline_case_ids: list[str]
    candidate_case_ids: list[str]
    model_profiles: dict[str, str]


class EvalReport(BaseModel):
    important_claim_citation_coverage: float
    citation_support_accuracy: float
    unsupported_important_claim_rate: float
    relative_quality_gain: float
    concurrency_verified: bool
    policy_bypass_count: int
    react_regressions: int
    cancel_timeout_partial_converged: bool
    judge_coverage: float = 0.0
    manual_review_case_ids: list[str] = Field(default_factory=list)


class ReleaseGateResult(BaseModel):
    passed: bool
    failures: list[str] = Field(default_factory=list)


async def compare_modes(
    cases: Sequence[EvalCase],
    executor: EvalExecutor,
    *,
    model_profile: str,
) -> ModeComparison:
    case_ids = [case.id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("eval case ids must be unique")

    baseline = [
        await executor.execute(case, AgentMode.REACT, model_profile)
        for case in cases
    ]
    candidate = [
        await executor.execute(
            case,
            AgentMode.RESEARCH_TEAM,
            model_profile,
        )
        for case in cases
    ]
    baseline_ids = [artifact.case_id for artifact in baseline]
    candidate_ids = [artifact.case_id for artifact in candidate]
    if baseline_ids != case_ids or candidate_ids != case_ids:
        raise ValueError("executor returned artifacts for different cases")

    return ModeComparison(
        baseline=baseline,
        candidate=candidate,
        baseline_case_ids=baseline_ids,
        candidate_case_ids=candidate_ids,
        model_profiles={
            AgentMode.REACT.value: model_profile,
            AgentMode.RESEARCH_TEAM.value: model_profile,
        },
    )


def evaluate_release_gate(report: EvalReport) -> ReleaseGateResult:
    checks = [
        (
            "important_claim_citation_coverage",
            report.important_claim_citation_coverage >= 0.95,
        ),
        (
            "citation_support_accuracy",
            report.citation_support_accuracy >= 0.90,
        ),
        (
            "unsupported_important_claim_rate",
            report.unsupported_important_claim_rate <= 0.03,
        ),
        ("relative_quality_gain", report.relative_quality_gain >= 0.15),
        ("concurrency_verified", report.concurrency_verified),
        ("policy_bypass_count", report.policy_bypass_count == 0),
        ("react_regressions", report.react_regressions == 0),
        (
            "cancel_timeout_partial_converged",
            report.cancel_timeout_partial_converged,
        ),
    ]
    failures = [name for name, passed in checks if not passed]
    return ReleaseGateResult(passed=not failures, failures=failures)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _artifact_metrics(
    case: EvalCase,
    artifact: EvalArtifact,
) -> ResearchQualityMetrics:
    return evaluate_report(ResearchEvaluationArtifact(
        claims=artifact.claims,
        sources=artifact.sources,
        required_topics=case.required_topics,
        covered_topics=artifact.covered_topics,
    ))


def _quality_score(
    metrics: ResearchQualityMetrics,
    judge: JudgeScores | None,
) -> float:
    deterministic = (
        metrics.important_claim_citation_coverage
        + metrics.citation_support_accuracy
        + (1 - metrics.unsupported_important_claim_rate)
        + metrics.required_topic_coverage
    ) / 4
    return (deterministic + judge.mean()) / 2 if judge else deterministic


def _manual_review_sample(cases: Sequence[EvalCase]) -> list[str]:
    grouped: dict[str, list[str]] = {}
    for case in cases:
        grouped.setdefault(case.category, []).append(case.id)
    selected: list[str] = []
    for category in sorted(grouped):
        case_ids = grouped[category]
        selected.extend(case_ids[:max(1, math.ceil(len(case_ids) * 0.2))])
    return selected


def score_comparison(
    cases: Sequence[EvalCase],
    comparison: ModeComparison,
) -> EvalReport:
    cases_by_id = {case.id: case for case in cases}
    if set(comparison.baseline_case_ids) != set(cases_by_id):
        raise ValueError("baseline artifacts do not match dataset")
    if comparison.baseline_case_ids != comparison.candidate_case_ids:
        raise ValueError("baseline and candidate cases differ")

    baseline_metrics = [
        _artifact_metrics(cases_by_id[item.case_id], item)
        for item in comparison.baseline
    ]
    candidate_metrics = [
        _artifact_metrics(cases_by_id[item.case_id], item)
        for item in comparison.candidate
    ]
    baseline_scores = [
        _quality_score(metrics, artifact.judge_scores)
        for metrics, artifact in zip(
            baseline_metrics,
            comparison.baseline,
            strict=True,
        )
    ]
    candidate_scores = [
        _quality_score(metrics, artifact.judge_scores)
        for metrics, artifact in zip(
            candidate_metrics,
            comparison.candidate,
            strict=True,
        )
    ]
    baseline_quality = _mean(baseline_scores)
    candidate_quality = _mean(candidate_scores)
    relative_gain = (
        (candidate_quality - baseline_quality) / baseline_quality
        if baseline_quality > 0
        else 1.0 if candidate_quality > 0 else 0.0
    )
    regressions = sum(
        candidate + 1e-9 < baseline
        for baseline, candidate in zip(
            baseline_scores,
            candidate_scores,
            strict=True,
        )
    )
    fault_case_ids = {
        case.id
        for case in cases
        if case.category in {"worker_failure", "budget_pressure"}
    }
    fault_artifacts = [
        artifact
        for artifact in comparison.candidate
        if artifact.case_id in fault_case_ids
    ]
    honest_terminal_statuses = {"completed", "partial", "cancelled"}

    return EvalReport(
        important_claim_citation_coverage=_mean([
            metrics.important_claim_citation_coverage
            for metrics in candidate_metrics
        ]),
        citation_support_accuracy=_mean([
            metrics.citation_support_accuracy
            for metrics in candidate_metrics
        ]),
        unsupported_important_claim_rate=_mean([
            metrics.unsupported_important_claim_rate
            for metrics in candidate_metrics
        ]),
        relative_quality_gain=relative_gain,
        concurrency_verified=any(
            artifact.max_parallel_workers >= 2
            for artifact in comparison.candidate
        ),
        policy_bypass_count=sum(
            artifact.policy_bypass_count
            for artifact in comparison.candidate
        ),
        react_regressions=regressions,
        cancel_timeout_partial_converged=(
            bool(fault_artifacts)
            and all(
                artifact.run_status in honest_terminal_statuses
                for artifact in fault_artifacts
            )
        ),
        judge_coverage=_mean([
            1.0 if artifact.judge_scores else 0.0
            for artifact in comparison.candidate
        ]),
        manual_review_case_ids=_manual_review_sample(cases),
    )


class HTTPExecutor:
    """Collects raw artifacts from an already-running, explicitly authorized API."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 1200) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def execute(
        self,
        case: EvalCase,
        mode: AgentMode,
        model_profile: str,
    ) -> EvalArtifact:
        started = asyncio.get_running_loop().time()
        timeout = httpx.Timeout(self._timeout)
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
        ) as client:
            created = await client.post("/api/sessions", json={})
            created.raise_for_status()
            session_id = created.json()["data"]["session_id"]
            raw_events = await self._stream_events(
                client,
                session_id,
                case,
                mode,
            )

        report = ""
        run_status = "completed"
        usage = RunUsage()
        sources: dict[str, ResearchSource] = {}
        active_tasks: set[str] = set()
        max_parallel_workers = 0
        policy_bypass_count = 0
        allowed_team_tools = {"search_web", "web_read"}
        terminal_task_statuses = {
            "completed",
            "failed",
            "skipped",
            "cancelled",
            "timed_out",
            "interrupted",
        }

        for event_name, data in raw_events:
            if event_name == "message" and data.get("role") == "assistant":
                report = data.get("message", report)
            elif event_name == "run":
                run_status = data.get("status", run_status)
                usage = RunUsage.model_validate(data.get("usage") or {})
            elif event_name == "research_source":
                source_data = dict(data["source"])
                source_data["object_storage_key"] = "not-collected"
                source = ResearchSource.model_validate(source_data)
                sources[source.id] = source
            elif event_name == "research_task":
                task_id = data.get("task_id") or data.get("task", {}).get("id")
                status = data.get("status")
                if task_id and status == "running":
                    active_tasks.add(task_id)
                    max_parallel_workers = max(
                        max_parallel_workers,
                        len(active_tasks),
                    )
                elif task_id and status in terminal_task_statuses:
                    active_tasks.discard(task_id)
            elif event_name == "tool" and data.get("run_id"):
                if data.get("function") not in allowed_team_tools:
                    policy_bypass_count += 1

        return EvalArtifact(
            case_id=case.id,
            mode=mode,
            model_profile=model_profile,
            final_report=report,
            run_status=run_status,
            sources=list(sources.values()),
            required_topics=case.required_topics,
            usage=usage,
            elapsed_ms=max(
                0,
                round((asyncio.get_running_loop().time() - started) * 1000),
            ),
            max_parallel_workers=max_parallel_workers,
            policy_bypass_count=policy_bypass_count,
        )

    async def _stream_events(
        self,
        client: httpx.AsyncClient,
        session_id: str,
        case: EvalCase,
        mode: AgentMode,
    ) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        current_event = "message"
        async with client.stream(
            "POST",
            f"/api/sessions/{session_id}/chat",
            json={
                "message": case.prompt,
                "attachments": [],
                "mode": mode.value,
                "budget_profile": "default",
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current_event = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    payload = json.loads(line.removeprefix("data:").strip())
                    events.append((current_event, payload))
                    current_event = "message"
        return events


def load_artifacts(path: str | Path) -> list[EvalArtifact]:
    artifacts = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            artifacts.append(EvalArtifact.model_validate_json(line))
        except ValueError as error:
            raise ValueError(
                f"invalid eval artifact at line {line_number}: {error}"
            ) from error
    return artifacts


def write_artifacts(path: str | Path, artifacts: Sequence[EvalArtifact]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(
            artifact.model_dump_json() + "\n"
            for artifact in artifacts
        ),
        encoding="utf-8",
    )


def _comparison_from_artifacts(
    baseline: list[EvalArtifact],
    candidate: list[EvalArtifact],
) -> ModeComparison:
    if not baseline or not candidate:
        raise ValueError("baseline and candidate artifacts must not be empty")
    if any(item.mode != AgentMode.REACT for item in baseline):
        raise ValueError("baseline artifacts must use react mode")
    if any(item.mode != AgentMode.RESEARCH_TEAM for item in candidate):
        raise ValueError("candidate artifacts must use research_team mode")
    baseline_profiles = {item.model_profile for item in baseline}
    candidate_profiles = {item.model_profile for item in candidate}
    if len(baseline_profiles) != 1 or baseline_profiles != candidate_profiles:
        raise ValueError("baseline and candidate must use one shared model profile")
    model_profile = next(iter(baseline_profiles))
    return ModeComparison(
        baseline=baseline,
        candidate=candidate,
        baseline_case_ids=[item.case_id for item in baseline],
        candidate_case_ids=[item.case_id for item in candidate],
        model_profiles={
            AgentMode.REACT.value: model_profile,
            AgentMode.RESEARCH_TEAM.value: model_profile,
        },
    )


async def _collect(args: argparse.Namespace) -> int:
    cases = load_cases(args.dataset)
    executor = HTTPExecutor(args.base_url)
    mode = AgentMode(args.mode)
    artifacts = [
        await executor.execute(case, mode, args.model_profile)
        for case in cases
    ]
    write_artifacts(args.output, artifacts)
    return 0


def _score(args: argparse.Namespace) -> int:
    cases = load_cases(args.dataset)
    comparison = _comparison_from_artifacts(
        load_artifacts(args.baseline),
        load_artifacts(args.candidate),
    )
    report = score_comparison(cases, comparison)
    gate = evaluate_release_gate(report)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "report": report.model_dump(mode="json"),
                "release_gate": gate.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    if gate.failures:
        print("Release gate failed: " + ", ".join(gate.failures))
    return 0 if gate.passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research Team evaluation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--base-url", required=True)
    collect.add_argument(
        "--mode",
        required=True,
        choices=[mode.value for mode in AgentMode],
    )
    collect.add_argument("--model-profile", default="default")
    collect.add_argument("--dataset", required=True)
    collect.add_argument("--output", required=True)

    score = subparsers.add_parser("score")
    score.add_argument("--dataset", required=True)
    score.add_argument("--baseline", required=True)
    score.add_argument("--candidate", required=True)
    score.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "collect":
        return asyncio.run(_collect(args))
    return _score(args)


if __name__ == "__main__":
    raise SystemExit(main())
