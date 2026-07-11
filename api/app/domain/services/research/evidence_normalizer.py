import hashlib
from collections.abc import Callable
from time import perf_counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain.external.source_content_storage import SourceContentStorage
from app.domain.external.web_reader import WebReader
from app.domain.models.research import (
    EvidenceExcerpt,
    FindingBundle,
    NormalizedFinding,
    ResearchClaim,
    ResearchSource,
)
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.research.telemetry import (
    NoopResearchTelemetry,
    ResearchTelemetry,
)


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


class InvalidFindingBundle(ValueError):
    pass


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if not scheme or not hostname:
        raise InvalidFindingBundle(f"invalid source URL: {url}")
    port = parsed.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname
    query = urlencode(sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
        and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ))
    return urlunsplit((scheme, netloc, parsed.path or "/", query, ""))


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


class EvidenceNormalizer:
    def __init__(
            self,
            reader: WebReader,
            source_storage: SourceContentStorage,
            uow_factory: Callable[[], IUnitOfWork],
            telemetry: ResearchTelemetry | None = None,
    ) -> None:
        self._reader = reader
        self._source_storage = source_storage
        self._uow_factory = uow_factory
        self._telemetry = telemetry or NoopResearchTelemetry()

    async def normalize(
            self,
            run_id: str,
            bundle: FindingBundle,
    ) -> NormalizedFinding:
        self._validate_local_references(bundle)

        async with self._uow_factory() as uow:
            existing_sources = await uow.research.list_sources(run_id)
            existing_evidence = await uow.research.list_evidence(run_id)

        sources_by_hash = {
            source.content_hash: source for source in existing_sources
        }
        evidence_by_key = {
            (item.source_id, item.excerpt_hash): item for item in existing_evidence
        }
        source_by_ref: dict[str, ResearchSource] = {}
        source_text_by_ref: dict[str, str] = {}
        new_sources: list[ResearchSource] = []

        for candidate in bundle.source_candidates:
            read_started = perf_counter()
            read_status = "failed"
            try:
                with self._telemetry.tool_span(
                    run_id=run_id,
                    task_id=bundle.task_id,
                    attempt_id=None,
                    tool_name="web_read",
                ):
                    result = await self._reader.read(candidate.original_url)
                read_status = "completed"
            finally:
                self._telemetry.record_tool_call(
                    tool_name="web_read",
                    status=read_status,
                    elapsed_ms=max(
                        0,
                        round((perf_counter() - read_started) * 1000),
                    ),
                )
            content_hash = hashlib.sha256(result.raw_content).hexdigest()
            source = sources_by_hash.get(content_hash)
            if source is None:
                object_key = await self._source_storage.put(
                    run_id=run_id,
                    content_hash=content_hash,
                    content=result.raw_content,
                    content_type=result.content_type,
                )
                canonical_url = canonicalize_url(result.final_url)
                source = ResearchSource(
                    run_id=run_id,
                    canonical_url=canonical_url,
                    original_url=candidate.original_url,
                    title=candidate.title or result.title or canonical_url,
                    domain=urlsplit(canonical_url).hostname or "",
                    publisher=candidate.metadata.get("publisher"),
                    retrieved_at=result.retrieved_at,
                    content_type=result.content_type,
                    content_hash=content_hash,
                    object_storage_key=object_key,
                    source_class=candidate.metadata.get("source_class", "unknown"),
                    metadata={
                        key: value
                        for key, value in candidate.metadata.items()
                        if key not in {"publisher", "source_class"}
                    },
                )
                sources_by_hash[content_hash] = source
                new_sources.append(source)
            source_by_ref[candidate.source_ref] = source
            source_text_by_ref[candidate.source_ref] = result.text

        evidence_by_ref: dict[str, EvidenceExcerpt] = {}
        new_evidence: list[EvidenceExcerpt] = []
        for candidate in bundle.evidence_candidates:
            source = source_by_ref[candidate.source_ref]
            excerpt = candidate.excerpt.strip()[:2_000]
            normalized_excerpt = _normalized_text(excerpt)
            normalized_source = _normalized_text(
                source_text_by_ref[candidate.source_ref]
            )
            if not normalized_excerpt or normalized_excerpt not in normalized_source:
                raise InvalidFindingBundle(
                    f"evidence excerpt not found in source: {candidate.evidence_ref}"
                )
            excerpt_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
            key = (source.id, excerpt_hash)
            evidence = evidence_by_key.get(key)
            if evidence is None:
                evidence = EvidenceExcerpt(
                    source_id=source.id,
                    run_id=run_id,
                    locator=candidate.locator,
                    excerpt=excerpt,
                    excerpt_hash=excerpt_hash,
                )
                evidence_by_key[key] = evidence
                new_evidence.append(evidence)
            evidence_by_ref[candidate.evidence_ref] = evidence

        claims: list[ResearchClaim] = []
        for candidate in bundle.claim_candidates:
            evidence_items = [
                evidence_by_ref[evidence_ref]
                for evidence_ref in candidate.evidence_refs
            ]
            self._validate_important_claim(
                candidate.importance,
                candidate.caveats,
                evidence_items,
                source_by_ref,
            )
            claims.append(ResearchClaim(
                run_id=run_id,
                task_id=bundle.task_id,
                text=candidate.text,
                importance=candidate.importance,
                confidence=candidate.confidence,
                caveats=candidate.caveats,
                evidence_ids=[item.id for item in evidence_items],
            ))

        async with self._uow_factory() as uow:
            for source in new_sources:
                await uow.research.add_source(source)
            for evidence in new_evidence:
                await uow.research.add_evidence(evidence)
            for claim in claims:
                await uow.research.add_claim(claim)

        unique_sources = list({source.id: source for source in source_by_ref.values()}.values())
        unique_evidence = list({item.id: item for item in evidence_by_ref.values()}.values())
        return NormalizedFinding(
            task_id=bundle.task_id,
            summary=bundle.summary,
            sources=unique_sources,
            evidence=unique_evidence,
            claims=claims,
            unresolved_questions=bundle.unresolved_questions,
            notes=bundle.notes,
        )

    @staticmethod
    def _validate_local_references(bundle: FindingBundle) -> None:
        source_refs = [item.source_ref for item in bundle.source_candidates]
        evidence_refs = [item.evidence_ref for item in bundle.evidence_candidates]
        claim_refs = [item.claim_ref for item in bundle.claim_candidates]
        for label, refs in {
            "source": source_refs,
            "evidence": evidence_refs,
            "claim": claim_refs,
        }.items():
            if len(refs) != len(set(refs)):
                raise InvalidFindingBundle(f"duplicate {label} reference")

        source_ref_set = set(source_refs)
        for evidence in bundle.evidence_candidates:
            if evidence.source_ref not in source_ref_set:
                raise InvalidFindingBundle(
                    f"unknown source reference: {evidence.source_ref}"
                )
        evidence_ref_set = set(evidence_refs)
        for claim in bundle.claim_candidates:
            missing = sorted(set(claim.evidence_refs) - evidence_ref_set)
            if missing:
                raise InvalidFindingBundle(
                    f"unknown evidence reference: {', '.join(missing)}"
                )

    @staticmethod
    def _validate_important_claim(
            importance: int,
            caveats: list[str],
            evidence: list[EvidenceExcerpt],
            source_by_ref: dict[str, ResearchSource],
    ) -> None:
        if importance < 3:
            return
        sources_by_id = {
            source.id: source for source in source_by_ref.values()
        }
        claim_sources = {
            sources_by_id[item.source_id].id: sources_by_id[item.source_id]
            for item in evidence
        }
        domains = {source.domain for source in claim_sources.values()}
        if len(domains) >= 2:
            return
        authoritative = any(
            source.source_class in {"official", "primary"}
            for source in claim_sources.values()
        )
        if authoritative and "single_authoritative_source" in caveats:
            return
        raise InvalidFindingBundle(
            "important claim requires two independent domains or an explicit "
            "single_authoritative_source caveat"
        )
