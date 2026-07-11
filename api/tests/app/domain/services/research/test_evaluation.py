import pytest

from app.domain.models.research import (
    ClaimSupportStatus,
    ResearchClaim,
    ResearchSource,
)
from app.domain.services.research.evaluation import (
    ResearchEvaluationArtifact,
    evaluate_report,
)


def _claim(
    claim_id: str,
    *,
    status: ClaimSupportStatus,
    cited: bool,
) -> ResearchClaim:
    return ResearchClaim(
        id=claim_id,
        run_id="run-1",
        task_id="task-1",
        text=f"Claim {claim_id}",
        importance=2,
        confidence=0.8,
        support_status=status,
        evidence_ids=[f"evidence-{claim_id}"] if cited else [],
    )


def _source(
    source_id: str,
    domain: str,
    content_hash: str,
) -> ResearchSource:
    return ResearchSource(
        id=source_id,
        run_id="run-1",
        canonical_url=f"https://{domain}/{content_hash}",
        original_url=f"https://{domain}/{content_hash}?utm_source=test",
        title=source_id,
        domain=domain,
        content_type="text/html",
        content_hash=content_hash,
        object_storage_key=f"research/run-1/{content_hash}",
    )


def test_quality_metrics_are_claim_weighted_and_support_aware() -> None:
    artifact = ResearchEvaluationArtifact(
        claims=[
            _claim(
                "supported",
                status=ClaimSupportStatus.SUPPORTED,
                cited=True,
            ),
            _claim(
                "partial",
                status=ClaimSupportStatus.PARTIALLY_SUPPORTED,
                cited=True,
            ),
            _claim(
                "unsupported",
                status=ClaimSupportStatus.UNSUPPORTED,
                cited=True,
            ),
            _claim(
                "uncited",
                status=ClaimSupportStatus.UNVERIFIED,
                cited=False,
            ),
        ],
        sources=[
            _source("source-1", "a.example", "same"),
            _source("source-2", "a.example", "same"),
            _source("source-3", "b.example", "unique"),
        ],
        required_topics=["topology", "persistence", "hitl", "protocol"],
        covered_topics=["topology", "persistence", "protocol"],
    )

    metrics = evaluate_report(artifact)

    assert metrics.important_claim_citation_coverage == pytest.approx(0.75)
    assert metrics.citation_support_accuracy == pytest.approx(2 / 3)
    assert metrics.unsupported_important_claim_rate == pytest.approx(0.5)
    assert metrics.independent_domain_count == 2
    assert metrics.source_count == 3
    assert metrics.duplicate_source_rate == pytest.approx(1 / 3)
    assert metrics.required_topic_coverage == pytest.approx(0.75)


def test_empty_report_has_zero_quality_without_division_errors() -> None:
    metrics = evaluate_report(ResearchEvaluationArtifact())

    assert metrics.important_claim_citation_coverage == 0
    assert metrics.citation_support_accuracy == 0
    assert metrics.unsupported_important_claim_rate == 0
    assert metrics.duplicate_source_rate == 0
    assert metrics.required_topic_coverage == 0
