from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.research import (
    EvidenceExcerpt,
    ResearchClaim,
    ResearchSource,
)
from app.infrastructure.models.base import Base


class ResearchSourceModel(Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_research_sources_id"),
        UniqueConstraint(
            "run_id",
            "content_hash",
            name="uq_research_sources_run_content_hash",
        ),
        Index("ix_research_sources_run_url", "run_id", "canonical_url"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    object_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_class: Mapped[str] = mapped_column(String(32), nullable=False)
    source_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False)

    @classmethod
    def from_domain(cls, source: ResearchSource) -> "ResearchSourceModel":
        return cls(
            id=source.id,
            run_id=source.run_id,
            canonical_url=source.canonical_url,
            original_url=source.original_url,
            title=source.title,
            domain=source.domain,
            publisher=source.publisher,
            published_at=source.published_at,
            retrieved_at=source.retrieved_at,
            content_type=source.content_type,
            content_hash=source.content_hash,
            object_storage_key=source.object_storage_key,
            source_class=source.source_class,
            source_metadata=source.metadata,
        )

    def to_domain(self) -> ResearchSource:
        return ResearchSource.model_validate({
            "id": self.id,
            "run_id": self.run_id,
            "canonical_url": self.canonical_url,
            "original_url": self.original_url,
            "title": self.title,
            "domain": self.domain,
            "publisher": self.publisher,
            "published_at": self.published_at,
            "retrieved_at": self.retrieved_at,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "object_storage_key": self.object_storage_key,
            "source_class": self.source_class,
            "metadata": self.source_metadata,
        })


class EvidenceExcerptModel(Base):
    __tablename__ = "evidence_excerpts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_evidence_excerpts_id"),
        UniqueConstraint(
            "source_id",
            "excerpt_hash",
            name="uq_evidence_excerpts_source_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("research_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    locator: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @classmethod
    def from_domain(cls, evidence: EvidenceExcerpt) -> "EvidenceExcerptModel":
        return cls(**evidence.model_dump(mode="python"))

    def to_domain(self) -> EvidenceExcerpt:
        return EvidenceExcerpt.model_validate(self, from_attributes=True)


class ResearchClaimModel(Base):
    __tablename__ = "research_claims"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_research_claims_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    caveats: Mapped[list] = mapped_column(JSONB, nullable=False)
    support_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @classmethod
    def from_domain(cls, claim: ResearchClaim) -> "ResearchClaimModel":
        return cls(
            id=claim.id,
            run_id=claim.run_id,
            task_id=claim.task_id,
            text=claim.text,
            importance=claim.importance,
            confidence=claim.confidence,
            caveats=claim.caveats,
            support_status=claim.support_status.value,
            created_at=claim.created_at,
        )

    def to_domain(self, evidence_ids: list[str] | None = None) -> ResearchClaim:
        return ResearchClaim.model_validate({
            "id": self.id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "text": self.text,
            "importance": self.importance,
            "confidence": self.confidence,
            "caveats": self.caveats,
            "support_status": self.support_status,
            "evidence_ids": evidence_ids or [],
            "created_at": self.created_at,
        })

    def update_from_domain(self, claim: ResearchClaim) -> None:
        replacement = ResearchClaimModel.from_domain(claim)
        for column in self.__table__.columns:
            if column.name not in {"id", "run_id", "task_id"}:
                setattr(self, column.name, getattr(replacement, column.name))


class ClaimEvidenceModel(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        PrimaryKeyConstraint(
            "claim_id",
            "evidence_id",
            name="pk_claim_evidence",
        ),
    )

    claim_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("research_claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("evidence_excerpts.id", ondelete="CASCADE"),
        nullable=False,
    )
