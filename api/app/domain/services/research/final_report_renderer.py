from app.domain.models.research import (
    ClaimSupportStatus,
    DraftReport,
    EvidenceExcerpt,
    ResearchClaim,
    ResearchSource,
)


VERIFIED_SUPPORT = {
    ClaimSupportStatus.SUPPORTED,
    ClaimSupportStatus.PARTIALLY_SUPPORTED,
}


class FinalReportRenderer:
    def render(
            self,
            draft: DraftReport,
            claims: list[ResearchClaim],
            evidence: list[EvidenceExcerpt],
            sources: list[ResearchSource],
    ) -> str:
        claims_by_id = {
            claim.id: claim
            for claim in claims
            if claim.support_status in VERIFIED_SUPPORT
        }
        evidence_by_id = {item.id: item for item in evidence}
        sources_by_id = {source.id: source for source in sources}
        source_numbers: dict[str, int] = {}
        lines = [f"# {draft.title}", "", draft.summary]

        for section in draft.sections:
            rendered_claims: list[str] = []
            for draft_claim in section.claims:
                claim = claims_by_id.get(draft_claim.claim_id)
                if claim is None:
                    continue
                claim_sources: list[ResearchSource] = []
                for evidence_id in claim.evidence_ids:
                    item = evidence_by_id.get(evidence_id)
                    if item is None:
                        continue
                    source = sources_by_id.get(item.source_id)
                    if source is not None and source not in claim_sources:
                        claim_sources.append(source)
                if not claim_sources:
                    continue
                citations = []
                for source in claim_sources:
                    if source.id not in source_numbers:
                        source_numbers[source.id] = len(source_numbers) + 1
                    number = source_numbers[source.id]
                    citations.append(f"[{number}]({source.original_url})")
                rendered_claims.append(
                    f"- {draft_claim.rendered_text} {' '.join(citations)}"
                )
            if rendered_claims:
                lines.extend(["", f"## {section.title}", "", *rendered_claims])

        if draft.limitations:
            lines.extend(["", "## 限制", ""])
            lines.extend(f"- {limitation}" for limitation in draft.limitations)

        if source_numbers:
            lines.extend(["", "## 来源", ""])
            ordered_sources = sorted(
                source_numbers,
                key=source_numbers.get,
            )
            for source_id in ordered_sources:
                source = sources_by_id[source_id]
                number = source_numbers[source_id]
                lines.append(
                    f"{number}. [{source.title}]({source.original_url})"
                    f" ({source.domain})"
                )

        return "\n".join(lines).strip() + "\n"
