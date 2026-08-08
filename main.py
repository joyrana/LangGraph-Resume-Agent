"""FastAPI orchestration layer for the resume optimization workflow."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from config import get_settings
from document_service import extract_resume_text
from resume_agent import get_resume_agent
from schemas import FeedbackRequest, FeedbackResponse, HealthResponse, SessionCreateResponse, SessionDetailResponse
from session_store import ResumeSession, store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
app = FastAPI(
    title="Resume ATS Optimizer API",
    description="FastAPI orchestration for a human-in-the-loop resume optimization workflow",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _serialize_session(session: ResumeSession) -> SessionDetailResponse:
    return SessionDetailResponse(
        session_id=session.session_id,
        status=session.status,
        ats_score=session.ats_score,
        analysis=session.analysis,
        draft_resume=session.draft_resume,
        candidate_name=session.candidate_name,
        job_description=session.job_description,
        company_details=session.company_details,
        feedback_round=session.feedback_round,
        final_download_url=session.final_download_url,
        original_file_name=session.original_file_name,
        created_at=session.created_at,
        updated_at=session.updated_at,
        feedback_history=session.feedback_history,
    )


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="healthy", message="Resume ATS Optimizer API is running")


@app.post("/api/sessions", response_model=SessionCreateResponse)
async def create_session(
    file: UploadFile = File(...),
    job_description: str = Form(...),
    company_details: str = Form(...),
    candidate_name: str | None = Form(default=None),
) -> SessionCreateResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="A resume file is required")

    try:
        resume_text = await extract_resume_text(file)
        session = store.create(
            original_file_name=file.filename,
            original_resume_text=resume_text,
            job_description=job_description,
            company_details=company_details,
            candidate_name=candidate_name,
        )

        agent = await get_resume_agent()
        result = await agent.generate(
            {
                "session_id": session.session_id,
                "original_resume_text": session.original_resume_text,
                "job_description": session.job_description,
                "company_details": session.company_details,
                "candidate_name": session.candidate_name,
                "approved": False,
                "feedback": "",
            }
        )

        session = store.update(
            session.session_id,
            ats_score=result.get("ats_score", 0),
            analysis=result.get("analysis", ""),
            draft_resume=result.get("draft_resume", ""),
            status="in_review",
        )
        return _serialize_session(session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create session")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str) -> SessionDetailResponse:
    try:
        return _serialize_session(store.get(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.post("/api/sessions/{session_id}/feedback", response_model=FeedbackResponse)
async def submit_feedback(session_id: str, request: FeedbackRequest) -> FeedbackResponse:
    try:
        session = store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    agent = await get_resume_agent()
    updated_session = store.append_feedback(session_id, request.feedback, request.approved)
    result = await agent.generate(
        {
            "session_id": updated_session.session_id,
            "original_resume_text": updated_session.original_resume_text,
            "job_description": updated_session.job_description,
            "company_details": updated_session.company_details,
            "candidate_name": updated_session.candidate_name,
            "approved": request.approved,
            "feedback": request.feedback,
        }
    )

    final_file_path = result.get("final_file_path")
    status = "finalized" if request.approved and final_file_path else "in_review"
    final_download_url = f"/api/sessions/{session_id}/download" if final_file_path else None

    session = store.update(
        session_id,
        ats_score=result.get("ats_score", session.ats_score),
        analysis=result.get("analysis", session.analysis),
        draft_resume=result.get("draft_resume", session.draft_resume),
        final_file_path=final_file_path,
        final_download_url=final_download_url,
        status=status,
    )

    return FeedbackResponse(
        session_id=session.session_id,
        status=session.status,
        ats_score=session.ats_score,
        analysis=session.analysis,
        draft_resume=session.draft_resume,
        feedback_round=session.feedback_round,
        final_download_url=session.final_download_url,
    )


@app.get("/api/sessions/{session_id}/download")
async def download_resume(session_id: str):
    try:
        session = store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    if not session.final_file_path:
        raise HTTPException(status_code=409, detail="Resume has not been finalized yet")

    file_path = Path(session.final_file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Final resume file is missing")

    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.on_event("startup")
async def startup_event() -> None:
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.generated_dir.mkdir(parents=True, exist_ok=True)
    await get_resume_agent()
    logger.info("Resume ATS Optimizer API started")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    logger.info("Resume ATS Optimizer API stopped")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level="info",
    )
