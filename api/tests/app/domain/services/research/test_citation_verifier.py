from app.domain.models.agent_run import CapabilityProfile
from app.domain.models.research import (
    CitationCheck,
    ClaimSupportStatus,
    DraftClaim,
    DraftReport,
    DraftSection,
    EvidenceExcerpt,
    ResearchClaim,
    ResearchSource,
)
from app.domain.services.research.citation_verifier import (
    CitationCheckBatch,
    CitationVerifier,
)


class FakeRuntime:
    def __init__(self, checks: list[CitationCheck]) -> None:
        self.checks = list(checks)
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.checks.pop(0)


class FakeStorage:
    async def get(self, object_key: str) -> bytes:
        if object_key == "missing":
            raise KeyError(object_key)
        return b"snapshot"


def citation_fixture():
    claim = ResearchClaim(
        id="claim-1",
        run_id="run-1",
        task_id="task-1",
        text="claim",
        importance=3,
        confidence=0.9,
        evidence_ids=["evidence-1"],
    )
    evidence = EvidenceExcerpt(
        id="evidence-1",
        source_id="source-1",
        run_id="run-1",
        locator="p1",
        excerpt="support",
        excerpt_hash="hash",
    )
    source = ResearchSource(
        id="source-1",
        run_id="run-1",
        canonical_url="https://verified.example",
        original_url="https://verified.example",
        title="Verified",
        domain="verified.example",
        content_type="text/html",
        content_hash="hash",
        object_storage_key="snapshot",
    )
    draft = DraftReport(
        title="Report",
        summary="Summary",
        sections=[
            DraftSection(
                title="Section",
                claims=[DraftClaim(claim_id="claim-1", rendered_text="claim")],
            )
        ],
    )
    return draft, claim, evidence, source


async def test_unsupported_claim_is_removed_from_verified_draft() -> None:
    draft, claim, evidence, source = citation_fixture()
    runtime = FakeRuntime([
        CitationCheck(
            claim_id="claim-1",
            status=ClaimSupportStatus.UNSUPPORTED,
            reason="not supported",
        )
    ])
    verifier = CitationVerifier(runtime=runtime, source_storage=FakeStorage())

    result = await verifier.verify(draft, [claim], [evidence], [source])

    assert result.checks[0].status == ClaimSupportStatus.UNSUPPORTED
    assert result.draft.sections[0].claims == []
    assert "claim-1" in result.draft.limitations[0]
    assert runtime.calls[0]["profile"] == CapabilityProfile.ANALYSIS


async def test_missing_snapshot_is_rejected_without_semantic_call() -> None:
    draft, claim, evidence, source = citation_fixture()
    source.object_storage_key = "missing"
    runtime = FakeRuntime([])
    verifier = CitationVerifier(runtime=runtime, source_storage=FakeStorage())

    result = await verifier.verify(draft, [claim], [evidence], [source])

    assert result.checks[0].status == ClaimSupportStatus.UNSUPPORTED
    assert runtime.calls == []


async def test_claims_are_semantically_verified_in_one_batch() -> None:
    _draft, claim, evidence, source = citation_fixture()
    runtime = FakeRuntime([CitationCheckBatch(checks=[CitationCheck(
        claim_id=claim.id,
        status=ClaimSupportStatus.SUPPORTED,
        reason="direct support",
    )])])
    verifier = CitationVerifier(runtime=runtime, source_storage=FakeStorage())

    checks = await verifier.verify_claims(
        [claim],
        [evidence],
        [source],
    )

    assert checks[0].status == ClaimSupportStatus.SUPPORTED
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["output_type"] is CitationCheckBatch
