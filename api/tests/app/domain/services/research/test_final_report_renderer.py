from app.domain.models.research import (
    ClaimSupportStatus,
    DraftClaim,
    DraftReport,
    DraftSection,
    EvidenceExcerpt,
    ResearchClaim,
    ResearchSource,
)
from app.domain.services.research.final_report_renderer import FinalReportRenderer


def test_renderer_only_links_verified_sources() -> None:
    draft = DraftReport(
        title="Report",
        summary="Summary",
        sections=[
            DraftSection(
                title="Facts",
                claims=[
                    DraftClaim(claim_id="verified", rendered_text="Verified fact"),
                    DraftClaim(claim_id="unsupported", rendered_text="Invented fact"),
                ],
            )
        ],
    )
    claims = [
        ResearchClaim(
            id="verified",
            run_id="run-1",
            task_id="task-1",
            text="Verified fact",
            importance=3,
            confidence=0.9,
            support_status=ClaimSupportStatus.SUPPORTED,
            evidence_ids=["evidence-1"],
        ),
        ResearchClaim(
            id="unsupported",
            run_id="run-1",
            task_id="task-1",
            text="Invented fact",
            importance=3,
            confidence=0.1,
            support_status=ClaimSupportStatus.UNSUPPORTED,
            evidence_ids=["evidence-2"],
        ),
    ]
    evidence = [
        EvidenceExcerpt(
            id="evidence-1",
            source_id="source-1",
            run_id="run-1",
            locator="p1",
            excerpt="support",
            excerpt_hash="h1",
        ),
        EvidenceExcerpt(
            id="evidence-2",
            source_id="source-2",
            run_id="run-1",
            locator="p2",
            excerpt="invented",
            excerpt_hash="h2",
        ),
    ]
    sources = [
        ResearchSource(
            id="source-1",
            run_id="run-1",
            canonical_url="https://verified.example",
            original_url="https://verified.example",
            title="Verified",
            domain="verified.example",
            content_type="text/html",
            content_hash="h1",
            object_storage_key="one",
        ),
        ResearchSource(
            id="source-2",
            run_id="run-1",
            canonical_url="https://invented.example",
            original_url="https://invented.example",
            title="Invented",
            domain="invented.example",
            content_type="text/html",
            content_hash="h2",
            object_storage_key="two",
        ),
    ]

    markdown = FinalReportRenderer().render(draft, claims, evidence, sources)

    assert "Verified fact" in markdown
    assert "https://verified.example" in markdown
    assert "Invented fact" not in markdown
    assert "invented.example" not in markdown

