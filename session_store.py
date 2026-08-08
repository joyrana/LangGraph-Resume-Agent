from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional
from uuid import uuid4


@dataclass
class ResumeSession:
    session_id: str
    original_file_name: str
    original_resume_text: str
    job_description: str
    company_details: str
    candidate_name: str | None = None
    ats_score: int = 0
    analysis: str = ""
    draft_resume: str = ""
    feedback_round: int = 0
    feedback_history: list[dict[str, Any]] = field(default_factory=list)
    final_file_path: str | None = None
    final_download_url: str | None = None
    status: str = "drafted"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, ResumeSession] = {}
        self._lock = Lock()

    def create(
        self,
        *,
        original_file_name: str,
        original_resume_text: str,
        job_description: str,
        company_details: str,
        candidate_name: str | None = None,
    ) -> ResumeSession:
        session = ResumeSession(
            session_id=str(uuid4()),
            original_file_name=original_file_name,
            original_resume_text=original_resume_text,
            job_description=job_description,
            company_details=company_details,
            candidate_name=candidate_name,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> ResumeSession:
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(session_id)
            return self._sessions[session_id]

    def update(self, session_id: str, **updates: Any) -> ResumeSession:
        with self._lock:
            session = self._sessions[session_id]
            for key, value in updates.items():
                setattr(session, key, value)
            session.updated_at = datetime.now(timezone.utc)
            return session

    def append_feedback(self, session_id: str, feedback: str, approved: bool) -> ResumeSession:
        with self._lock:
            session = self._sessions[session_id]
            session.feedback_round += 1
            session.feedback_history.append(
                {
                    "round": session.feedback_round,
                    "feedback": feedback,
                    "approved": approved,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            session.updated_at = datetime.now(timezone.utc)
            return session


store = SessionStore()
