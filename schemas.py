from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    ats_score: int
    analysis: str
    draft_resume: str
    candidate_name: Optional[str] = None
    job_description: str
    company_details: str
    feedback_round: int = 0
    final_download_url: Optional[str] = None


class SessionDetailResponse(SessionCreateResponse):
    original_file_name: str
    created_at: datetime
    updated_at: datetime
    feedback_history: list[dict[str, Any]] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    feedback: str = Field(min_length=1)
    approved: bool = False


class FeedbackResponse(BaseModel):
    session_id: str
    status: str
    ats_score: int
    analysis: str
    draft_resume: str
    feedback_round: int
    final_download_url: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    message: str


class SessionStatus(str):
    DRAFTED = "drafted"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    FINALIZED = "finalized"
    ERROR = "error"


class ResumeGenerationResult(BaseModel):
    ats_score: int
    analysis: str
    draft_resume: str
    revision_notes: str
    final_file_path: Optional[str] = None
    final_download_url: Optional[str] = None
