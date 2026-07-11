from pathlib import Path

from app.domain.services.research.evaluation import load_cases


DATASET = Path(__file__).parents[2] / "evals" / "research_cases.jsonl"


def test_dataset_has_required_categories_and_unique_ids() -> None:
    cases = load_cases(DATASET)

    assert len(cases) == 30
    assert len({case.id for case in cases}) == 30
    assert {case.category for case in cases} >= {
        "breadth",
        "comparison",
        "freshness",
        "conflict",
        "single_authority",
        "no_reliable_answer",
        "duplicate_results",
        "prompt_injection",
        "worker_failure",
        "budget_pressure",
    }


def test_prompt_injection_cases_are_local_fixtures() -> None:
    cases = load_cases(DATASET)
    injection_cases = [
        case for case in cases if case.category == "prompt_injection"
    ]

    assert len(injection_cases) == 3
    assert all(case.injected_source_text for case in injection_cases)
    assert all(case.fault_profile is None for case in injection_cases)
