"""Proposal schemas: what the model may return, and what the system stores."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EditType(str, Enum):
    GRAMMAR = "grammar"
    CLARITY = "clarity"
    IMPACT = "impact"
    KEYWORD_ALIGNMENT = "keyword_alignment"
    CONCISION = "concision"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Decision(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


# ---------------------------------------------------------------- model output (untrusted)


class LLMEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(max_length=40)
    quote: str = Field(min_length=1, max_length=400)


class LLMProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(max_length=40)
    original_text: str = Field(min_length=1, max_length=600)
    proposed_text: str = Field(max_length=800)
    edit_type: EditType
    reason: str = Field(min_length=1, max_length=500)
    supporting_evidence: list[LLMEvidence] = Field(min_length=1, max_length=4)
    confidence: float = Field(ge=0.0, le=1.0)


class LLMProposalBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alignment_summary: str = Field(default="", max_length=1500)
    gaps_without_evidence: list[str] = Field(default_factory=list, max_length=20)
    proposals: list[LLMProposal] = Field(default_factory=list, max_length=60)


def llm_output_schema() -> dict:
    """JSON schema sent to the provider for constrained decoding."""
    return LLMProposalBatch.model_json_schema()


# ---------------------------------------------------------------- validated (stored)


class Evidence(BaseModel):
    location_id: str
    quote: str
    section: str | None = None


class EditProposal(BaseModel):
    edit_id: str
    idempotency_key: str
    source_document_version: str
    location_id: str
    section: str | None
    container: str
    original_text: str
    proposed_text: str
    start: int
    end: int
    paragraph_text: str
    edit_type: EditType
    reason: str
    supporting_evidence: list[Evidence]
    model_confidence: float = Field(description="Self-reported by the model; not evidence of correctness.")
    risk_level: RiskLevel
    requires_user_confirmation: bool
    validation_warnings: list[str] = Field(default_factory=list)
    decision: Decision = Decision.PENDING


class RejectedProposal(BaseModel):
    location_id: str
    original_text: str
    proposed_text: str
    code: str
    message: str


class AlignmentEstimate(BaseModel):
    """Deterministic keyword coverage. An estimate, not an ATS score."""

    method: str = "keyword_coverage_v1"
    label: str = "Estimated keyword coverage (not an ATS score; no guarantee of screening outcome)"
    coverage_percent: int | None = None
    matched_terms: list[str] = Field(default_factory=list)
    missing_terms: list[str] = Field(default_factory=list)
    terms_considered: int = 0
