import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.research import (
    ClaimSupportStatus,
    ResearchClaim,
    ResearchSource,
)


EvalCategory = Literal[
    "breadth",
    "comparison",
    "freshness",
    "conflict",
    "single_authority",
    "no_reliable_answer",
    "duplicate_results",
    "prompt_injection",
    "worker_failure",
    "budget_pressure",
]


class ResearchQualityMetrics(BaseModel):
    important_claim_citation_coverage: float = Field(ge=0, le=1)
    citation_support_accuracy: float = Field(ge=0, le=1)
    unsupported_important_claim_rate: float = Field(ge=0, le=1)
    independent_domain_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    duplicate_source_rate: float = Field(ge=0, le=1)
    required_topic_coverage: float = Field(ge=0, le=1)


class ResearchEvaluationArtifact(BaseModel):
    claims: list[ResearchClaim] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    required_topics: list[str] = Field(default_factory=list)
    covered_topics: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    category: EvalCategory
    prompt: str = Field(min_length=1)
    required_topics: list[str] = Field(min_length=1)
    preferred_source_types: list[str] = Field(min_length=1)
    freshness_days: int | None = Field(default=None, ge=1)
    authoritative_domains: list[str] = Field(default_factory=list)
    injected_source_text: str | None = None
    fault_profile: str | None = None


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _normalized_topic(topic: str) -> str:
    return " ".join(topic.casefold().split())


def evaluate_report(
    artifact: ResearchEvaluationArtifact,
) -> ResearchQualityMetrics:
    important_claims = [
        claim for claim in artifact.claims if claim.importance >= 2
    ]
    important_weight = sum(claim.importance for claim in important_claims)
    cited_claims = [claim for claim in important_claims if claim.evidence_ids]
    cited_weight = sum(claim.importance for claim in cited_claims)
    supported_statuses = {
        ClaimSupportStatus.SUPPORTED,
        ClaimSupportStatus.PARTIALLY_SUPPORTED,
    }
    supported_cited_weight = sum(
        claim.importance
        for claim in cited_claims
        if claim.support_status in supported_statuses
    )
    unsupported_weight = sum(
        claim.importance
        for claim in important_claims
        if claim.support_status not in supported_statuses
    )

    source_fingerprints = {
        source.content_hash or source.canonical_url
        for source in artifact.sources
    }
    source_count = len(artifact.sources)
    duplicate_count = source_count - len(source_fingerprints)
    required_topics = {
        _normalized_topic(topic)
        for topic in artifact.required_topics
        if _normalized_topic(topic)
    }
    covered_topics = {
        _normalized_topic(topic)
        for topic in artifact.covered_topics
        if _normalized_topic(topic)
    }

    return ResearchQualityMetrics(
        important_claim_citation_coverage=_ratio(
            cited_weight,
            important_weight,
        ),
        citation_support_accuracy=_ratio(
            supported_cited_weight,
            cited_weight,
        ),
        unsupported_important_claim_rate=_ratio(
            unsupported_weight,
            important_weight,
        ),
        independent_domain_count=len({
            source.domain.casefold()
            for source in artifact.sources
            if source.domain
        }),
        source_count=source_count,
        duplicate_source_rate=_ratio(duplicate_count, source_count),
        required_topic_coverage=_ratio(
            len(required_topics & covered_topics),
            len(required_topics),
        ),
    )


def load_cases(path: str | Path) -> list[EvalCase]:
    dataset_path = Path(path)
    cases: list[EvalCase] = []
    for line_number, raw_line in enumerate(
        dataset_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            cases.append(EvalCase.model_validate(json.loads(raw_line)))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(
                f"invalid eval case at line {line_number}: {error}"
            ) from error

    case_ids = [case.id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("eval case ids must be unique")
    return cases
