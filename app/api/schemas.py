"""Request/response schemas (plain Pydantic; used by the service layer and FastAPI)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.document.document_models import FeatureInventory
from app.document.fidelity_models import FidelityReport
from app.resume.proposal_models import AlignmentEstimate, Decision, EditProposal


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class DocumentSummary(BaseModel):
    file_name: str
    document_version: str
    paragraphs: int
    editable_paragraphs: int
    sections: int
    features: FeatureInventory
    limitations: list[str]
    upload_warnings: list[str] = Field(default_factory=list)


class DiffSegment(BaseModel):
    op: Literal["equal", "insert", "delete"]
    text: str


class ProposalView(EditProposal):
    confirmed: bool = False
    diff: list[DiffSegment]
    paragraph_after: str


class AnalysisView(BaseModel):
    alignment_summary: str
    gaps_without_evidence: list[str]
    alignment_estimate: AlignmentEstimate
    rejected_proposal_count: int
    rejected_reasons: dict[str, int]
    context_truncated: bool
    model: dict[str, Any]


class ErrorInfo(BaseModel):
    code: str
    message: str


class FinalizationView(BaseModel):
    finalization_id: str
    decision: Literal["PASS", "REVIEW_REQUIRED", "FAIL"]
    created_at: str
    review_revision: int
    is_current: bool = Field(description="True when it matches the currently accepted edits.")
    acknowledged: bool
    downloadable: bool
    blocked_reason: str | None = None
    edit_failures: list[dict[str, Any]] = Field(default_factory=list)
    report: FidelityReport | None = None


class SessionView(BaseModel):
    session_id: str
    status: str
    created_at: str
    updated_at: str
    expires_at: str
    review_revision: int
    document: DocumentSummary
    analysis: AnalysisView | None = None
    proposals: list[ProposalView] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    error: ErrorInfo | None = None
    latest_finalization: FinalizationView | None = None


class SessionCreated(BaseModel):
    session_id: str
    session_token: str = Field(description="Capability token. Send as X-Session-Token on every request for this session.")
    session: SessionView


class DecisionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edit_id: str = Field(max_length=40)
    decision: Decision
    confirm_high_risk: bool = False


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_revision: int = Field(ge=0)
    decision: Decision
    confirm_high_risk: bool = False


class BulkDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_revision: int = Field(ge=0)
    decisions: list[DecisionItem] = Field(min_length=1, max_length=100)


class FinalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_revision: int = Field(ge=0)


class DownloadLink(BaseModel):
    url: str
    expires_at: int


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, Any] = Field(default_factory=dict)
