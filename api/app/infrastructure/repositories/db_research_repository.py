from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.research import (
    EvidenceExcerpt,
    ResearchClaim,
    ResearchSource,
)
from app.domain.repositories.research_repository import ResearchRepository
from app.infrastructure.models.research import (
    ClaimEvidenceModel,
    EvidenceExcerptModel,
    ResearchClaimModel,
    ResearchSourceModel,
)


class DBResearchRepository(ResearchRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def add_source(self, source: ResearchSource) -> None:
        self.db_session.add(ResearchSourceModel.from_domain(source))

    async def get_source(self, source_id: str) -> ResearchSource | None:
        result = await self.db_session.execute(
            select(ResearchSourceModel).where(ResearchSourceModel.id == source_id)
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def list_sources(self, run_id: str) -> list[ResearchSource]:
        result = await self.db_session.execute(
            select(ResearchSourceModel)
            .where(ResearchSourceModel.run_id == run_id)
            .order_by(ResearchSourceModel.retrieved_at, ResearchSourceModel.id)
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def add_evidence(self, evidence: EvidenceExcerpt) -> None:
        self.db_session.add(EvidenceExcerptModel.from_domain(evidence))

    async def list_evidence(self, run_id: str) -> list[EvidenceExcerpt]:
        result = await self.db_session.execute(
            select(EvidenceExcerptModel)
            .where(EvidenceExcerptModel.run_id == run_id)
            .order_by(EvidenceExcerptModel.created_at, EvidenceExcerptModel.id)
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def add_claim(self, claim: ResearchClaim) -> None:
        self.db_session.add(ResearchClaimModel.from_domain(claim))
        if claim.evidence_ids:
            self.db_session.add_all([
                ClaimEvidenceModel(claim_id=claim.id, evidence_id=evidence_id)
                for evidence_id in claim.evidence_ids
            ])

    async def update_claim(self, claim: ResearchClaim) -> None:
        result = await self.db_session.execute(
            select(ResearchClaimModel).where(ResearchClaimModel.id == claim.id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise ValueError(f"research claim not found: {claim.id}")
        record.update_from_domain(claim)
        await self.db_session.execute(
            delete(ClaimEvidenceModel).where(ClaimEvidenceModel.claim_id == claim.id)
        )
        if claim.evidence_ids:
            self.db_session.add_all([
                ClaimEvidenceModel(claim_id=claim.id, evidence_id=evidence_id)
                for evidence_id in claim.evidence_ids
            ])

    async def list_claims(self, run_id: str) -> list[ResearchClaim]:
        claim_result = await self.db_session.execute(
            select(ResearchClaimModel)
            .where(ResearchClaimModel.run_id == run_id)
            .order_by(ResearchClaimModel.created_at, ResearchClaimModel.id)
        )
        records = list(claim_result.scalars().all())
        if not records:
            return []

        claim_ids = [record.id for record in records]
        link_result = await self.db_session.execute(
            select(ClaimEvidenceModel).where(
                ClaimEvidenceModel.claim_id.in_(claim_ids)
            )
        )
        evidence_map: dict[str, list[str]] = {claim_id: [] for claim_id in claim_ids}
        for link in link_result.scalars().all():
            evidence_map[link.claim_id].append(link.evidence_id)
        return [
            record.to_domain(sorted(evidence_map[record.id]))
            for record in records
        ]
