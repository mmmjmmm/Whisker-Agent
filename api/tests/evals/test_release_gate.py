from evals.run_research_eval import EvalReport, evaluate_release_gate


def _passing_report(**overrides) -> EvalReport:
    values = {
        "important_claim_citation_coverage": 0.96,
        "citation_support_accuracy": 0.91,
        "unsupported_important_claim_rate": 0.02,
        "relative_quality_gain": 0.16,
        "concurrency_verified": True,
        "policy_bypass_count": 0,
        "react_regressions": 0,
        "cancel_timeout_partial_converged": True,
    }
    values.update(overrides)
    return EvalReport(**values)


def test_release_gate_requires_every_threshold() -> None:
    result = evaluate_release_gate(
        _passing_report(relative_quality_gain=0.14)
    )

    assert result.passed is False
    assert result.failures == ["relative_quality_gain"]


def test_release_gate_lists_all_failures_in_stable_order() -> None:
    result = evaluate_release_gate(EvalReport(
        important_claim_citation_coverage=0.5,
        citation_support_accuracy=0.5,
        unsupported_important_claim_rate=0.5,
        relative_quality_gain=0,
        concurrency_verified=False,
        policy_bypass_count=1,
        react_regressions=1,
        cancel_timeout_partial_converged=False,
    ))

    assert result.failures == [
        "important_claim_citation_coverage",
        "citation_support_accuracy",
        "unsupported_important_claim_rate",
        "relative_quality_gain",
        "concurrency_verified",
        "policy_bypass_count",
        "react_regressions",
        "cancel_timeout_partial_converged",
    ]


def test_release_gate_passes_only_when_every_threshold_passes() -> None:
    result = evaluate_release_gate(_passing_report())

    assert result.passed is True
    assert result.failures == []
