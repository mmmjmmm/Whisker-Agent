import pytest

from app.domain.models.agent_run import AgentMode
from app.domain.models.research import ClaimSupportStatus, ResearchClaim
from app.domain.services.research.evaluation import EvalCase
from evals.run_research_eval import (
    EvalArtifact,
    compare_modes,
    main,
    write_artifacts,
)


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentMode, str]] = []

    async def execute(
        self,
        case: EvalCase,
        mode: AgentMode,
        model_profile: str,
    ) -> EvalArtifact:
        self.calls.append((case.id, mode, model_profile))
        return EvalArtifact(
            case_id=case.id,
            mode=mode,
            model_profile=model_profile,
            final_report=f"{mode.value}:{case.id}",
            run_status="completed",
            required_topics=case.required_topics,
            covered_topics=case.required_topics,
            elapsed_ms=10,
        )


def _case(case_id: str, category: str = "comparison") -> EvalCase:
    return EvalCase(
        id=case_id,
        category=category,
        prompt="compare",
        required_topics=["topology"],
        preferred_source_types=["official"],
    )


@pytest.mark.asyncio
async def test_runner_uses_same_cases_and_model_profile() -> None:
    executor = FakeExecutor()
    cases = [_case("case-1"), _case("case-2")]

    result = await compare_modes(
        cases,
        executor,
        model_profile="default",
    )

    assert result.baseline_case_ids == result.candidate_case_ids
    assert result.baseline_case_ids == ["case-1", "case-2"]
    assert result.model_profiles == {
        "react": "default",
        "research_team": "default",
    }
    assert executor.calls == [
        ("case-1", AgentMode.REACT, "default"),
        ("case-2", AgentMode.REACT, "default"),
        ("case-1", AgentMode.RESEARCH_TEAM, "default"),
        ("case-2", AgentMode.RESEARCH_TEAM, "default"),
    ]


@pytest.mark.asyncio
async def test_runner_rejects_duplicate_case_ids() -> None:
    with pytest.raises(ValueError, match="unique"):
        await compare_modes(
            [_case("duplicate"), _case("duplicate")],
            FakeExecutor(),
            model_profile="default",
        )


def test_score_command_returns_zero_when_release_gate_passes(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        _case("fault-case", "worker_failure").model_dump_json() + "\n",
        encoding="utf-8",
    )
    baseline_path = tmp_path / "baseline.jsonl"
    candidate_path = tmp_path / "candidate.jsonl"
    report_path = tmp_path / "report.json"
    baseline = EvalArtifact(
        case_id="fault-case",
        mode=AgentMode.REACT,
        model_profile="default",
        final_report="unsupported",
        run_status="completed",
        required_topics=["topology"],
        elapsed_ms=10,
    )
    candidate = EvalArtifact(
        case_id="fault-case",
        mode=AgentMode.RESEARCH_TEAM,
        model_profile="default",
        final_report="verified",
        run_status="partial",
        claims=[ResearchClaim(
            run_id="run-1",
            task_id="task-1",
            text="verified",
            importance=3,
            confidence=1,
            support_status=ClaimSupportStatus.SUPPORTED,
            evidence_ids=["evidence-1"],
        )],
        required_topics=["topology"],
        covered_topics=["topology"],
        elapsed_ms=10,
        max_parallel_workers=2,
    )
    write_artifacts(baseline_path, [baseline])
    write_artifacts(candidate_path, [candidate])

    exit_code = main([
        "score",
        "--dataset",
        str(dataset),
        "--baseline",
        str(baseline_path),
        "--candidate",
        str(candidate_path),
        "--output",
        str(report_path),
    ])

    assert exit_code == 0
    assert '"passed": true' in report_path.read_text(encoding="utf-8")


def test_score_command_returns_one_when_release_gate_fails(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        _case("case-1").model_dump_json() + "\n",
        encoding="utf-8",
    )
    baseline_path = tmp_path / "baseline.jsonl"
    candidate_path = tmp_path / "candidate.jsonl"
    report_path = tmp_path / "report.json"
    common = {
        "case_id": "case-1",
        "model_profile": "default",
        "final_report": "",
        "run_status": "failed",
        "required_topics": ["topology"],
        "elapsed_ms": 10,
    }
    write_artifacts(
        baseline_path,
        [EvalArtifact(mode=AgentMode.REACT, **common)],
    )
    write_artifacts(
        candidate_path,
        [EvalArtifact(mode=AgentMode.RESEARCH_TEAM, **common)],
    )

    exit_code = main([
        "score",
        "--dataset",
        str(dataset),
        "--baseline",
        str(baseline_path),
        "--candidate",
        str(candidate_path),
        "--output",
        str(report_path),
    ])

    assert exit_code == 1
    assert '"passed": false' in report_path.read_text(encoding="utf-8")
