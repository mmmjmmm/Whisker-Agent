from app.domain.external.web_reader import WebReadResult
from app.domain.models.research import (
    ClaimCandidate,
    EvidenceCandidate,
    FindingBundle,
    SourceCandidate,
)
from app.domain.services.research.evidence_normalizer import (
    EvidenceNormalizer,
    InvalidFindingBundle,
)


class FakeReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read(self, url: str) -> WebReadResult:
        self.calls.append(url)
        return WebReadResult(
            requested_url=url,
            final_url="https://example.com/article?utm_source=test",
            title="Example",
            content_type="text/html",
            text="A verified fact appears here.",
            raw_content=b"A verified fact appears here.",
            retrieved_at="2026-07-10T00:00:00Z",
            response_headers={},
        )


class FakeSourceStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, run_id, content_hash, content, content_type) -> str:
        key = f"research/{run_id}/{content_hash}"
        self.objects[key] = content
        return key


class FakeResearchRepository:
    def __init__(self) -> None:
        self.sources = []
        self.evidence = []
        self.claims = []

    async def list_sources(self, run_id):
        return [source for source in self.sources if source.run_id == run_id]

    async def list_evidence(self, run_id):
        return [item for item in self.evidence if item.run_id == run_id]

    async def add_source(self, source) -> None:
        self.sources.append(source)

    async def add_evidence(self, evidence) -> None:
        self.evidence.append(evidence)

    async def add_claim(self, claim) -> None:
        self.claims.append(claim)


class FakeUow:
    def __init__(self, research) -> None:
        self.research = research

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


def finding_bundle(evidence_refs: list[str]) -> FindingBundle:
    return FindingBundle(
        task_id="task-1",
        summary="summary",
        source_candidates=[
            SourceCandidate(
                source_ref="source-local",
                original_url="https://example.com/article",
                metadata={"source_class": "official"},
            )
        ],
        evidence_candidates=[
            EvidenceCandidate(
                evidence_ref="evidence-local",
                source_ref="source-local",
                locator="p1",
                excerpt="A verified fact",
            )
        ],
        claim_candidates=[
            ClaimCandidate(
                claim_ref="claim-local",
                text="A verified fact",
                importance=3,
                confidence=0.9,
                caveats=["single_authoritative_source"],
                evidence_refs=evidence_refs,
            )
        ],
    )


async def test_normalizer_rejects_unknown_evidence_reference() -> None:
    reader = FakeReader()
    repository = FakeResearchRepository()
    normalizer = EvidenceNormalizer(
        reader=reader,
        source_storage=FakeSourceStorage(),
        uow_factory=lambda: FakeUow(repository),
    )

    try:
        await normalizer.normalize("run-1", finding_bundle(["missing"]))
    except InvalidFindingBundle as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("unknown evidence reference was accepted")

    assert reader.calls == []


async def test_normalizer_persists_local_reference_mapping() -> None:
    repository = FakeResearchRepository()
    storage = FakeSourceStorage()
    normalizer = EvidenceNormalizer(
        reader=FakeReader(),
        source_storage=storage,
        uow_factory=lambda: FakeUow(repository),
    )

    result = await normalizer.normalize(
        "run-1",
        finding_bundle(["evidence-local"]),
    )

    assert len(result.sources) == 1
    assert result.sources[0].canonical_url == "https://example.com/article"
    assert len(result.evidence) == 1
    assert result.claims[0].evidence_ids == [result.evidence[0].id]
    assert repository.claims[0].evidence_ids == [repository.evidence[0].id]
    assert storage.objects


async def test_normalizer_rejects_excerpt_not_present_in_source() -> None:
    repository = FakeResearchRepository()
    bundle = finding_bundle(["evidence-local"])
    bundle.evidence_candidates[0].excerpt = "invented excerpt"
    normalizer = EvidenceNormalizer(
        reader=FakeReader(),
        source_storage=FakeSourceStorage(),
        uow_factory=lambda: FakeUow(repository),
    )

    try:
        await normalizer.normalize("run-1", bundle)
    except InvalidFindingBundle as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("invented excerpt was accepted")

