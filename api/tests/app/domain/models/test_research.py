import pytest
from pydantic import ValidationError

from app.domain.models.research import (
    ClaimCandidate,
    EvidenceCandidate,
    FindingBundle,
    ResearchPlan,
    ResearchTaskSpec,
    SourceCandidate,
)


def test_finding_bundle_uses_local_evidence_references() -> None:
    bundle = FindingBundle(
        task_id="task-1",
        summary="summary",
        source_candidates=[
            SourceCandidate(source_ref="s1", original_url="https://example.com")
        ],
        evidence_candidates=[
            EvidenceCandidate(
                evidence_ref="e1",
                source_ref="s1",
                locator="p1",
                excerpt="fact",
            )
        ],
        claim_candidates=[
            ClaimCandidate(claim_ref="c1", text="claim", evidence_refs=["e1"])
        ],
    )

    assert bundle.claim_candidates[0].evidence_refs == ["e1"]


def test_claim_candidate_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        ClaimCandidate(claim_ref="c1", text="claim", evidence_refs=[])


def test_research_plan_requires_at_least_one_task() -> None:
    with pytest.raises(ValidationError):
        ResearchPlan(title="title", goal="goal", tasks=[])


def test_task_key_rejects_spaces() -> None:
    with pytest.raises(ValidationError):
        ResearchTaskSpec(
            key="invalid key",
            description="description",
            objective="objective",
            capability_profile="analysis",
            acceptance_criteria=["complete"],
        )

