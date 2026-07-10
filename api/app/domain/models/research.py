import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models.agent_run import (
    AgentTask,
    CapabilityProfile,
    RunStatus,
    TaskStatus,
    utc_now,
)


class SourceStrategy(BaseModel):
    freshness_requirement: str | None = None
    preferred_source_types: list[str] = Field(default_factory=list)
    minimum_independent_domains: int = Field(default=2, ge=1, le=10)
    known_authoritative_sources: list[str] = Field(default_factory=list)


class ResearchTaskSpec(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    capability_profile: CapabilityProfile
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    source_requirements: dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    priority: int = 0


class ResearchPlan(BaseModel):
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    language: str = "zh-CN"
    source_strategy: SourceStrategy = Field(default_factory=SourceStrategy)
    tasks: list[ResearchTaskSpec] = Field(min_length=1)


class SourceCandidate(BaseModel):
    source_ref: str = Field(min_length=1)
    original_url: str = Field(min_length=1)
    title: str | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceCandidate(BaseModel):
    evidence_ref: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class ClaimCandidate(BaseModel):
    claim_ref: str = Field(min_length=1)
    text: str = Field(min_length=1)
    importance: int = Field(default=1, ge=1, le=3)
    confidence: float = Field(default=0.5, ge=0, le=1)
    caveats: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(min_length=1)


class FindingBundle(BaseModel):
    task_id: str
    summary: str
    source_candidates: list[SourceCandidate] = Field(default_factory=list)
    evidence_candidates: list[EvidenceCandidate] = Field(default_factory=list)
    claim_candidates: list[ClaimCandidate] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class WorkerContext(BaseModel):
    run_id: str
    goal: str
    task: AgentTask
    dependency_summaries: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    attachment_summaries: list[str] = Field(default_factory=list)
    remaining_attempts: int = Field(ge=0)


class ClaimSupportStatus(str, Enum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"


class ResearchSource(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    canonical_url: str
    original_url: str
    title: str
    domain: str
    publisher: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    content_type: str
    content_hash: str
    object_storage_key: str
    source_class: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceExcerpt(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    run_id: str
    locator: str
    excerpt: str
    excerpt_hash: str
    created_at: datetime = Field(default_factory=utc_now)


class ResearchClaim(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    task_id: str
    text: str
    importance: int = Field(ge=1, le=3)
    confidence: float = Field(ge=0, le=1)
    caveats: list[str] = Field(default_factory=list)
    support_status: ClaimSupportStatus = ClaimSupportStatus.UNVERIFIED
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ReviewContext(BaseModel):
    run_id: str
    goal: str
    plan: ResearchPlan
    tasks: list[AgentTask]
    claims: list[ResearchClaim]
    evidence: list[EvidenceExcerpt]
    sources: list[ResearchSource]


class ReviewResult(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    missing_questions: list[str] = Field(default_factory=list)
    repair_tasks: list[ResearchTaskSpec] = Field(default_factory=list)


class DraftClaim(BaseModel):
    claim_id: str
    rendered_text: str


class DraftSection(BaseModel):
    title: str
    claims: list[DraftClaim] = Field(default_factory=list)


class DraftReport(BaseModel):
    title: str
    summary: str
    sections: list[DraftSection]
    limitations: list[str] = Field(default_factory=list)


class CitationCheck(BaseModel):
    claim_id: str
    status: ClaimSupportStatus
    reason: str


class NormalizedFinding(BaseModel):
    task_id: str
    summary: str
    sources: list[ResearchSource] = Field(default_factory=list)
    evidence: list[EvidenceExcerpt] = Field(default_factory=list)
    claims: list[ResearchClaim] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CitationVerificationResult(BaseModel):
    draft: DraftReport
    checks: list[CitationCheck] = Field(default_factory=list)


class AttachmentIssue(BaseModel):
    file_id: str
    code: str
    message: str


class AttachmentIngestResult(BaseModel):
    sources: list[ResearchSource] = Field(default_factory=list)
    evidence: list[EvidenceExcerpt] = Field(default_factory=list)
    issues: list[AttachmentIssue] = Field(default_factory=list)


class OrchestrationResult(BaseModel):
    run_status: RunStatus
    status_by_key: dict[str, TaskStatus]
    findings: list[NormalizedFinding] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
