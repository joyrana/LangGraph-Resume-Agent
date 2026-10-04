"""FastAPI application: thin HTTP adapter over ``ResumeService``."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import (
    BulkDecisionRequest,
    DecisionItem,
    DecisionRequest,
    DownloadLink,
    ErrorBody,
    ErrorEnvelope,
    FinalizationView,
    FinalizeRequest,
    HealthResponse,
    SessionCreated,
    SessionView,
)
from app.core import security
from app.core.config import Settings, get_settings
from app.core.errors import AppError, InvalidState, PayloadTooLarge, Unauthorized
from app.core.logging import configure_logging, correlation_id_var
from app.document.visual_renderer import LibreOfficeRenderer
from app.llm.provider import build_provider
from app.persistence.artifact_store import ArtifactStore
from app.persistence.repositories import Repository
from app.services.resume_service import ResumeService

logger = logging.getLogger(__name__)
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ERROR_RESPONSES = {code: {"model": ErrorEnvelope} for code in (401, 404, 409, 413, 422, 500, 502, 503)}


def _envelope(code: str, message: str, status_code: int, details: dict | None = None) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorBody(code=code, message=message, details=details or {}, correlation_id=correlation_id_var.get()))
    return JSONResponse(status_code=status_code, content=body.model_dump())


def create_app(settings: Settings | None = None, service: ResumeService | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_content)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = service
        if svc is None:
            repo = Repository(settings.resolved_database_path)
            store = ArtifactStore(settings.storage_dir / "artifacts")
            renderer = LibreOfficeRenderer(
                settings.soffice_path, settings.pdftoppm_path, settings.renderer_timeout_seconds, settings.render_dpi
            )
            svc = ResumeService(settings, repo, store, build_provider(settings), renderer)
        app.state.service = svc
        app.state.tasks = set()
        if settings.llm_is_remote and not settings.llm_external_provider_acknowledged:
            logger.warning(
                "llm_endpoint_not_local",
                extra={
                    "hint": "Resume text will be sent to a non-local model endpoint. Set RESUME_LLM_EXTERNAL_PROVIDER_ACKNOWLEDGED=true after disclosing this to users."
                },
            )
        svc.purge_expired()

        async def purge_loop() -> None:
            while True:
                await asyncio.sleep(600)
                try:
                    await asyncio.to_thread(svc.purge_expired)
                except Exception:
                    logger.exception("purge_failed")

        purger = asyncio.create_task(purge_loop())
        logger.info(
            "startup",
            extra={"llm_provider": settings.llm_provider, "model": settings.ollama_model, "deployment_mode": settings.deployment_mode},
        )
        try:
            yield
        finally:
            purger.cancel()
            for task in list(app.state.tasks):
                task.cancel()
            await svc.provider.close()
            svc.repo.close()

    app = FastAPI(
        title="Resume Edit Agent API",
        description="Evidence-backed, user-approved edits to an uploaded DOCX resume, with layout-preservation delivery gates.",
        version="0.3.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.deployment_mode == "local" or settings.debug else None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Session-Token", "X-Correlation-ID"],
    )

    max_body = settings.max_upload_bytes + settings.max_job_description_chars * 4 + 256 * 1024

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-correlation-id", "")
        cid = incoming if 8 <= len(incoming) <= 64 and incoming.replace("-", "").isalnum() else uuid.uuid4().hex
        token = correlation_id_var.set(cid)
        try:
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > max_body:
                return _envelope(PayloadTooLarge.code, "The request is too large.", 413)
            if settings.api_auth_token and request.url.path.startswith("/api/") and not request.url.path.startswith("/api/downloads/"):
                supplied = request.headers.get("authorization", "")
                if not security.tokens_match(supplied.removeprefix("Bearer ").strip(), security.hash_token(settings.api_auth_token)):
                    return _envelope(Unauthorized.code, Unauthorized.default_message, 401)
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        if exc.status_code >= 500:
            logger.warning("app_error", extra={"code": exc.code, "status": exc.status_code})
        return _envelope(exc.code, exc.message, exc.status_code, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        problems = [{"field": ".".join(str(p) for p in err.get("loc", [])[1:]), "message": err.get("msg", "")} for err in exc.errors()]
        return _envelope("invalid_request", "The request is invalid.", 422, {"problems": problems})

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(_: Request, exc: StarletteHTTPException):
        return _envelope("http_error", str(exc.detail) if exc.status_code < 500 else "Server error.", exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception):
        logger.error("unhandled_error", extra={"error_type": type(exc).__name__})
        return _envelope("internal_error", "An unexpected error occurred.", 500)

    # ------------------------------------------------------------------ dependencies
    def svc(request: Request) -> ResumeService:
        return request.app.state.service

    Service = Annotated[ResumeService, Depends(svc)]
    SessionToken = Annotated[str | None, Header(alias="X-Session-Token")]

    def spawn_analysis(request: Request, service_: ResumeService, session_id: str) -> None:
        task = asyncio.create_task(service_.run_analysis(session_id))
        request.app.state.tasks.add(task)
        task.add_done_callback(request.app.state.tasks.discard)

    # ------------------------------------------------------------------ health
    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/ready", response_model=HealthResponse, tags=["health"], responses={503: {"model": HealthResponse}})
    async def ready(service_: Service, response: Response) -> HealthResponse:
        checks: dict = {}
        try:
            checks["database"] = service_.repo.ping()
        except Exception:
            checks["database"] = False
        renderer = service_.evaluator.renderer
        checks["renderer"] = {
            "available": bool(renderer and renderer.available()),
            **service_.evaluator.renderer_info().model_dump(include={"version", "expected_version", "pinned"}),
        }
        checks["llm"] = {"provider": settings.llm_provider, "model": settings.ollama_model, "remote_endpoint": settings.llm_is_remote}
        ok = checks["database"] and (checks["renderer"]["available"] or not settings.visual_gate_required)
        if not ok:
            response.status_code = 503
        return HealthResponse(status="ready" if ok else "not_ready", checks=checks)

    # ------------------------------------------------------------------ sessions
    @app.post(
        "/api/sessions", response_model=SessionCreated, status_code=status.HTTP_202_ACCEPTED, responses=ERROR_RESPONSES, tags=["sessions"]
    )
    async def create_session(
        request: Request,
        service_: Service,
        file: Annotated[UploadFile, File(description="The resume as a Word .docx file")],
        job_description: Annotated[str, Form()],
        company_details: Annotated[str, Form()] = "",
        candidate_notes: Annotated[str, Form(description="Optional facts the candidate confirms (e.g. metrics) that edits may use.")] = "",
    ) -> SessionCreated:
        data = await file.read(settings.max_upload_bytes + 1)
        created = await asyncio.to_thread(
            service_.create_session,
            data=data,
            file_name=file.filename,
            content_type=file.content_type,
            job_description=job_description,
            company_details=company_details,
            candidate_notes=candidate_notes,
        )
        spawn_analysis(request, service_, created.session_id)
        return created

    @app.get("/api/sessions/{session_id}", response_model=SessionView, responses=ERROR_RESPONSES, tags=["sessions"])
    async def get_session(session_id: str, service_: Service, x_session_token: SessionToken = None) -> SessionView:
        return service_.get_session(session_id, x_session_token)

    @app.post(
        "/api/sessions/{session_id}/analysis", response_model=SessionView, status_code=202, responses=ERROR_RESPONSES, tags=["sessions"]
    )
    async def retry_analysis(session_id: str, request: Request, service_: Service, x_session_token: SessionToken = None) -> SessionView:
        session = service_.authorize(session_id, x_session_token)
        if not service_.retry_analysis_allowed(session):
            raise InvalidState("Analysis can only be retried after it failed.")
        spawn_analysis(request, service_, session.id)
        return service_.get_session(session_id, x_session_token)

    @app.delete("/api/sessions/{session_id}", status_code=204, responses=ERROR_RESPONSES, tags=["sessions"])
    async def delete_session(session_id: str, service_: Service, x_session_token: SessionToken = None) -> Response:
        service_.delete_session(session_id, x_session_token)
        return Response(status_code=204)

    @app.get("/api/sessions/{session_id}/events", responses=ERROR_RESPONSES, tags=["sessions"])
    async def events(session_id: str, service_: Service, x_session_token: SessionToken = None) -> list[dict]:
        return service_.audit_trail(session_id, x_session_token)

    # ------------------------------------------------------------------ review
    @app.put(
        "/api/sessions/{session_id}/proposals/{edit_id}/decision", response_model=SessionView, responses=ERROR_RESPONSES, tags=["review"]
    )
    async def decide_one(
        session_id: str, edit_id: str, body: DecisionRequest, service_: Service, x_session_token: SessionToken = None
    ) -> SessionView:
        item = DecisionItem(edit_id=edit_id, decision=body.decision, confirm_high_risk=body.confirm_high_risk)
        return service_.decide(session_id, x_session_token, body.review_revision, [item])

    @app.post("/api/sessions/{session_id}/decisions", response_model=SessionView, responses=ERROR_RESPONSES, tags=["review"])
    async def decide_bulk(
        session_id: str, body: BulkDecisionRequest, service_: Service, x_session_token: SessionToken = None
    ) -> SessionView:
        return service_.decide(session_id, x_session_token, body.review_revision, body.decisions)

    # ------------------------------------------------------------------ finalization and delivery
    @app.post("/api/sessions/{session_id}/finalize", response_model=FinalizationView, responses=ERROR_RESPONSES, tags=["delivery"])
    async def finalize(session_id: str, body: FinalizeRequest, service_: Service, x_session_token: SessionToken = None) -> FinalizationView:
        return await service_.finalize(session_id, x_session_token, body.review_revision)

    @app.get(
        "/api/sessions/{session_id}/finalizations/{finalization_id}",
        response_model=FinalizationView,
        responses=ERROR_RESPONSES,
        tags=["delivery"],
    )
    async def get_finalization(
        session_id: str, finalization_id: str, service_: Service, x_session_token: SessionToken = None
    ) -> FinalizationView:
        return service_.get_finalization(session_id, x_session_token, finalization_id)

    @app.post(
        "/api/sessions/{session_id}/finalizations/{finalization_id}/acknowledge",
        response_model=FinalizationView,
        responses=ERROR_RESPONSES,
        tags=["delivery"],
    )
    async def acknowledge(
        session_id: str, finalization_id: str, service_: Service, x_session_token: SessionToken = None
    ) -> FinalizationView:
        return service_.acknowledge(session_id, x_session_token, finalization_id)

    @app.post(
        "/api/sessions/{session_id}/finalizations/{finalization_id}/download-link",
        response_model=DownloadLink,
        responses=ERROR_RESPONSES,
        tags=["delivery"],
    )
    async def download_link(session_id: str, finalization_id: str, service_: Service, x_session_token: SessionToken = None) -> DownloadLink:
        return service_.issue_download_link(session_id, x_session_token, finalization_id)

    @app.get("/api/sessions/{session_id}/finalizations/{finalization_id}/artifacts/{name}", responses=ERROR_RESPONSES, tags=["delivery"])
    async def report_artifact(
        session_id: str, finalization_id: str, name: str, service_: Service, x_session_token: SessionToken = None
    ) -> Response:
        data = service_.report_artifact(session_id, x_session_token, finalization_id, name)
        return Response(content=data, media_type="image/png")

    @app.get("/api/downloads/{token}", responses=ERROR_RESPONSES, tags=["delivery"])
    async def download(token: str, service_: Service) -> Response:
        payload = await asyncio.to_thread(service_.resolve_download, token)
        safe = payload.file_name.encode("ascii", "ignore").decode().replace('"', "") or "resume-edited.docx"
        return Response(
            content=payload.data,
            media_type=DOCX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{safe}"', "Cache-Control": "no-store"},
        )

    return app
