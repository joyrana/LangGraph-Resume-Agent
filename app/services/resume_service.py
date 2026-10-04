"""Application service: every use case, independent of the web framework."""

from __future__ import annotations

import difflib
import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.api.schemas import (
    AnalysisView,
    DecisionItem,
    DiffSegment,
    DocumentSummary,
    DownloadLink,
    ErrorInfo,
    FinalizationView,
    ProposalView,
    SessionCreated,
    SessionView,
)
from app.core import security
from app.core.config import Settings
from app.core.errors import AppError, DeliveryBlocked, InputTooLong, InvalidState, NotFound, StaleReview, UploadRejected
from app.document.document_models import DocumentIndex
from app.document.docx_parser import parse_docx
from app.document.fidelity_evaluator import FidelityEvaluator
from app.document.fidelity_models import FidelityReport, GateStatus
from app.document.upload_validator import UploadLimits, validate_docx_upload
from app.document.visual_diff import VisualThresholds
from app.document.visual_renderer import LibreOfficeRenderer
from app.llm.provider import LLMProvider
from app.persistence.artifact_store import ArtifactStore
from app.persistence.repositories import FinalizationRecord, ProposalRecord, Repository, SessionRecord, SessionStatus
from app.resume.proposal_models import AlignmentEstimate, Decision, RiskLevel
from app.workflow.graph import build_finalize_graph, build_proposal_graph
from app.workflow.nodes import WorkflowDeps

logger = logging.getLogger(__name__)
_TOKEN_SPLIT = re.compile(r"\w+|\s+|[^\w\s]")


def word_diff(before: str, after: str) -> list[DiffSegment]:
    a, b = _TOKEN_SPLIT.findall(before), _TOKEN_SPLIT.findall(after)
    out: list[DiffSegment] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(DiffSegment(op="equal", text="".join(a[i1:i2])))
            continue
        if i2 > i1:
            out.append(DiffSegment(op="delete", text="".join(a[i1:i2])))
        if j2 > j1:
            out.append(DiffSegment(op="insert", text="".join(b[j1:j2])))
    return out


def accepted_set_key(document_version: str, accepted: list[tuple[str, str]]) -> str:
    """Identity of an exact approved edit set (used as the finalization idempotency key)."""
    body = "|".join(f"{eid}:{hashlib.sha256(text.encode()).hexdigest()}" for eid, text in sorted(accepted))
    return hashlib.sha256(f"{document_version}#{body}".encode()).hexdigest()


@dataclass
class DownloadPayload:
    data: bytes
    file_name: str


class ResumeService:
    def __init__(
        self, settings: Settings, repo: Repository, store: ArtifactStore, provider: LLMProvider, renderer: LibreOfficeRenderer | None
    ) -> None:
        self.settings = settings
        self.repo = repo
        self.store = store
        self.provider = provider
        self.limits = UploadLimits(
            settings.max_upload_bytes, settings.max_uncompressed_bytes, settings.max_zip_members, settings.max_compression_ratio
        )
        self.evaluator = FidelityEvaluator(
            self.limits,
            renderer if settings.renderer_enabled else None,
            VisualThresholds(),
            expected_renderer_version=settings.renderer_expected_version,
            visual_gate_required=settings.visual_gate_required,
        )
        self.deps = WorkflowDeps(settings=settings, repo=repo, store=store, provider=provider, evaluator=self.evaluator)
        self.proposal_graph = build_proposal_graph(self.deps)
        self.finalize_graph = build_finalize_graph(self.deps)

    # ------------------------------------------------------------------ auth
    def authorize(self, session_id: str, token: str | None) -> SessionRecord:
        """Missing session and wrong token are indistinguishable (no enumeration)."""
        if not re.fullmatch(r"[a-f0-9]{32}", session_id or ""):
            raise NotFound("Session not found.")
        try:
            session = self.repo.get_session(session_id)
        except NotFound:
            raise NotFound("Session not found.") from None
        if not security.tokens_match(token, session.token_hash):
            raise NotFound("Session not found.")
        if session.expires_at < datetime.now(UTC).isoformat():
            raise NotFound("This session has expired and its files were scheduled for deletion.")
        return session

    # ------------------------------------------------------------------ create
    def create_session(
        self,
        *,
        data: bytes,
        file_name: str | None,
        content_type: str | None,
        job_description: str,
        company_details: str,
        candidate_notes: str,
    ) -> SessionCreated:
        s = self.settings
        if not job_description.strip():
            raise InputTooLong("A job description is required.", details={"field": "job_description"})
        for field_name, value, limit in (
            ("job_description", job_description, s.max_job_description_chars),
            ("company_details", company_details, s.max_company_details_chars),
            ("candidate_notes", candidate_notes, s.max_candidate_notes_chars),
        ):
            if len(value) > limit:
                raise InputTooLong(
                    f"{field_name.replace('_', ' ').capitalize()} is longer than {limit} characters.",
                    details={"field": field_name, "limit": limit},
                )
        report = validate_docx_upload(data, file_name, content_type, self.limits)
        try:
            index, _ = parse_docx(data)
        except Exception as exc:
            raise UploadRejected("The Word document structure could not be read.", details={"reason": "unparseable"}) from exc
        if not any(b.editable and b.text.strip() for b in index.blocks):
            raise UploadRejected(
                "No editable text was found in this document (for example, all text is inside text boxes).",
                details={"reason": "no_editable_text"},
            )

        token = security.new_session_token()
        session = self.repo.create_session(
            token_hash=security.hash_token(token),
            original_file_name=report.safe_file_name,
            document_version=index.document_version,
            job_description=job_description.strip(),
            company_details=company_details.strip(),
            candidate_notes=candidate_notes.strip(),
            ttl_hours=s.session_ttl_hours,
        )
        original = self.store.put(session.id, "original", data, read_only=True)
        self.repo.add_artifact(original)
        idx_artifact = self.store.put(session.id, "index", index.model_dump_json().encode(), read_only=True)
        self.repo.add_artifact(idx_artifact)
        if report.warnings:
            self.repo.record_event(session.id, "upload_warnings", data={"warnings": report.warnings})
        logger.info("session_created", extra={"session_id": session.id, "size_bytes": report.size_bytes, "paragraphs": len(index.blocks)})
        return SessionCreated(session_id=session.id, session_token=token, session=self._view(session, upload_warnings=report.warnings))

    # ------------------------------------------------------------------ analysis
    def analysis_lease_seconds(self) -> float:
        s = self.settings
        return s.llm_timeout_seconds * (s.llm_max_retries + 1) * 2 + 60

    async def run_analysis(self, session_id: str) -> None:
        if not self.repo.claim_analysis(session_id, self.analysis_lease_seconds()):
            return  # someone else holds the lease, or analysis already completed
        try:
            await self.proposal_graph.ainvoke({"session_id": session_id})
        except AppError as exc:
            self.repo.fail_analysis(session_id, exc.code, exc.message)
        except Exception:
            logger.exception("analysis_crashed", extra={"session_id": session_id})
            self.repo.fail_analysis(session_id, "internal_error", "Analysis failed unexpectedly. Try again.")

    def retry_analysis_allowed(self, session: SessionRecord) -> bool:
        return session.status == SessionStatus.ANALYSIS_FAILED

    # ------------------------------------------------------------------ views
    def get_session(self, session_id: str, token: str | None) -> SessionView:
        return self._view(self.authorize(session_id, token))

    def _index(self, session_id: str) -> DocumentIndex:
        return DocumentIndex.model_validate_json(self.store.get(self.repo.find_artifact(session_id, "index")))

    def _view(self, session: SessionRecord, upload_warnings: list[str] | None = None) -> SessionView:
        index = self._index(session.id)
        if upload_warnings is None:
            upload_warnings = next(
                (e["data"].get("warnings", []) for e in self.repo.events(session.id) if e["event"] == "upload_warnings"), []
            )
        document = DocumentSummary(
            file_name=session.original_file_name,
            document_version=session.document_version,
            paragraphs=len(index.blocks),
            editable_paragraphs=sum(1 for b in index.blocks if b.editable and b.text.strip()),
            sections=len(index.sections),
            features=index.features,
            limitations=index.limitations,
            upload_warnings=upload_warnings,
        )
        records = self.repo.list_proposals(session.id)
        proposals = [self._proposal_view(r) for r in records]
        analysis = None
        if session.analysis:
            a = session.analysis
            rejected = a.get("rejected_proposals", [])
            analysis = AnalysisView(
                alignment_summary=a.get("alignment_summary", ""),
                gaps_without_evidence=a.get("gaps_without_evidence", []),
                alignment_estimate=AlignmentEstimate.model_validate(a.get("alignment_estimate", {})),
                rejected_proposal_count=len(rejected),
                rejected_reasons=dict(Counter(r["code"] for r in rejected)),
                context_truncated=bool(a.get("context_truncated")),
                model={k: a.get("model", {}).get(k) for k in ("provider", "model", "prompt_version", "latency_ms", "attempts")},
            )
        counts = Counter(r.decision.value for r in records)
        latest = self.repo.latest_finalization(session.id)
        return SessionView(
            session_id=session.id,
            status=session.status,
            created_at=session.created_at,
            updated_at=session.updated_at,
            expires_at=session.expires_at,
            review_revision=session.review_revision,
            document=document,
            analysis=analysis,
            proposals=proposals,
            counts={"total": len(records), **{d.value: counts.get(d.value, 0) for d in Decision}},
            error=ErrorInfo(code=session.error_code, message=session.error_message or "") if session.error_code else None,
            latest_finalization=self._finalization_view(session, latest, records, include_report=False) if latest else None,
        )

    @staticmethod
    def _proposal_view(record: ProposalRecord) -> ProposalView:
        p = record.proposal
        after = p.paragraph_text[: p.start] + p.proposed_text + p.paragraph_text[p.end :]
        return ProposalView(
            **p.model_dump(), confirmed=record.confirmed, diff=word_diff(p.original_text, p.proposed_text), paragraph_after=after
        )

    # ------------------------------------------------------------------ decisions
    def decide(self, session_id: str, token: str | None, review_revision: int, items: list[DecisionItem]) -> SessionView:
        session = self.authorize(session_id, token)
        records = {r.proposal.edit_id: r for r in self.repo.list_proposals(session.id)}
        updates: list[tuple[str, Decision, bool]] = []
        seen: set[str] = set()
        for item in items:
            if item.edit_id in seen:
                raise InvalidState("The same edit appears twice in one request.")
            seen.add(item.edit_id)
            record = records.get(item.edit_id)
            if record is None:
                raise NotFound(f"Unknown edit {item.edit_id}.")
            if record.proposal.source_document_version != session.document_version:
                raise StaleReview("This proposal belongs to a different document version.")
            needs_confirmation = record.proposal.requires_user_confirmation and item.decision == Decision.ACCEPTED
            if needs_confirmation and not item.confirm_high_risk:
                raise InvalidState(
                    "This edit makes a stronger claim than your resume states. Confirm it is accurate to accept it.",
                    details={"edit_id": item.edit_id, "reason": "confirmation_required"},
                )
            updates.append((item.edit_id, item.decision, bool(needs_confirmation and item.confirm_high_risk)))
        self.repo.set_decisions(session.id, review_revision, updates)
        return self._view(self.repo.get_session(session.id))

    # ------------------------------------------------------------------ finalization
    def _accepted(self, session: SessionRecord, records: list[ProposalRecord]) -> list[ProposalRecord]:
        return [r for r in records if r.decision == Decision.ACCEPTED]

    def _current_key(self, session: SessionRecord, records: list[ProposalRecord]) -> str:
        return accepted_set_key(
            session.document_version, [(r.proposal.edit_id, r.proposal.proposed_text) for r in self._accepted(session, records)]
        )

    async def finalize(self, session_id: str, token: str | None, review_revision: int) -> FinalizationView:
        session = self.authorize(session_id, token)
        if session.review_revision != review_revision:
            raise StaleReview()
        records = self.repo.list_proposals(session.id)
        accepted = self._accepted(session, records)
        if not accepted:
            raise InvalidState("Accept at least one edit before finalizing.", details={"reason": "no_accepted_edits"})
        for r in accepted:
            if r.proposal.risk_level == RiskLevel.HIGH and not r.confirmed:
                raise InvalidState("A high-risk edit was accepted without confirmation.", details={"edit_id": r.proposal.edit_id})
        key = self._current_key(session, records)
        existing = self.repo.find_finalization_by_key(session.id, key)
        if existing is not None:
            # Idempotent: the same approved set always yields the same, already-evaluated result.
            return self._finalization_view(self.repo.get_session(session.id), existing, records, include_report=True)

        lease = self.settings.renderer_timeout_seconds * 3 + 120
        self.repo.begin_finalization(session.id, review_revision, lease)
        try:
            state = await self.finalize_graph.ainvoke(
                {
                    "session_id": session.id,
                    "review_revision": review_revision,
                    "idempotency_key": key,
                    "accepted_edit_ids": sorted(r.proposal.edit_id for r in accepted),
                }
            )
        except Exception as exc:
            self.repo.abort_finalization(session.id, type(exc).__name__)
            if isinstance(exc, AppError):
                raise
            logger.exception("finalization_crashed", extra={"session_id": session.id})
            raise AppError("Finalization failed unexpectedly. Your decisions are saved; try again.") from exc
        record = self.repo.get_finalization(session.id, state["finalization_id"])
        logger.info("finalized", extra={"session_id": session.id, "decision": record.decision, "accepted": len(accepted)})
        return self._finalization_view(self.repo.get_session(session.id), record, self.repo.list_proposals(session.id), include_report=True)

    def _report(self, record: FinalizationRecord) -> FidelityReport | None:
        if not record.report_artifact_id:
            return None
        return FidelityReport.model_validate_json(self.store.get(self.repo.get_artifact(record.session_id, record.report_artifact_id)))

    def _gate(self, session: SessionRecord, record: FinalizationRecord, records: list[ProposalRecord]) -> tuple[bool, bool, str | None]:
        """Return (is_current, downloadable, blocked_reason)."""
        is_current = record.idempotency_key == self._current_key(session, records)
        if record.decision == GateStatus.FAIL.value or not record.output_artifact_id:
            return is_current, False, "The edited document failed the delivery gates."
        if not is_current:
            return is_current, False, "Your decisions changed after this result. Finalize again."
        if record.decision == GateStatus.REVIEW_REQUIRED.value and not record.acknowledged_at:
            return is_current, False, "Review and acknowledge the layout warnings before downloading."
        return is_current, True, None

    def _finalization_view(
        self, session: SessionRecord, record: FinalizationRecord, records: list[ProposalRecord], include_report: bool
    ) -> FinalizationView:
        is_current, downloadable, reason = self._gate(session, record, records)
        return FinalizationView(
            finalization_id=record.id,
            decision=record.decision,  # type: ignore[arg-type]
            created_at=record.created_at,
            review_revision=record.review_revision,
            is_current=is_current,
            acknowledged=bool(record.acknowledged_at),
            downloadable=downloadable,
            blocked_reason=reason,
            edit_failures=record.summary.get("edit_failures", []),
            report=self._report(record) if include_report else None,
        )

    def get_finalization(self, session_id: str, token: str | None, finalization_id: str) -> FinalizationView:
        session = self.authorize(session_id, token)
        record = self.repo.get_finalization(session.id, finalization_id)
        return self._finalization_view(session, record, self.repo.list_proposals(session.id), include_report=True)

    def acknowledge(self, session_id: str, token: str | None, finalization_id: str) -> FinalizationView:
        session = self.authorize(session_id, token)
        record = self.repo.acknowledge(session.id, finalization_id)
        return self._finalization_view(session, record, self.repo.list_proposals(session.id), include_report=True)

    # ------------------------------------------------------------------ downloads
    def issue_download_link(self, session_id: str, token: str | None, finalization_id: str) -> DownloadLink:
        session = self.authorize(session_id, token)
        record = self.repo.get_finalization(session.id, finalization_id)
        _, downloadable, reason = self._gate(session, record, self.repo.list_proposals(session.id))
        if not downloadable:
            raise DeliveryBlocked(reason)
        signed, expires = security.sign_download_token(
            self.settings.signing_secret, session.id, record.id, self.settings.download_link_ttl_seconds
        )
        self.repo.record_event(session.id, "download_link_issued", data={"finalization_id": record.id})
        return DownloadLink(url=f"/api/downloads/{signed}", expires_at=expires)

    def resolve_download(self, signed: str) -> DownloadPayload:
        claim = security.verify_download_token(self.settings.signing_secret, signed)
        try:
            session = self.repo.get_session(claim.session_id)
            record = self.repo.get_finalization(session.id, claim.finalization_id)
        except NotFound:
            raise NotFound("The file is no longer available.") from None
        if session.expires_at < datetime.now(UTC).isoformat():
            raise NotFound("The file is no longer available.")
        _, downloadable, reason = self._gate(session, record, self.repo.list_proposals(session.id))
        if not downloadable:
            raise DeliveryBlocked(reason)
        data = self.store.get(self.repo.get_artifact(session.id, record.output_artifact_id or ""))
        self.repo.record_event(session.id, "downloaded", data={"finalization_id": record.id})
        stem = Path(session.original_file_name).stem[:100] or "resume"
        return DownloadPayload(data=data, file_name=f"{stem}-edited.docx")

    def report_artifact(self, session_id: str, token: str | None, finalization_id: str, name: str) -> bytes:
        session = self.authorize(session_id, token)
        record = self.repo.get_finalization(session.id, finalization_id)
        report = self._report(record)
        if report is None or name not in report.artifacts or not re.fullmatch(r"visual_diff_page_\d{1,3}", name):
            raise NotFound()
        file_name = report.artifacts[name]
        if not re.fullmatch(r"visual_diff_page_\d{1,3}\.png", file_name):
            raise NotFound()
        path = self.store.work_dir(session.id, "fid_" + record.idempotency_key[:16]) / file_name
        if not path.is_file():
            raise NotFound()
        return path.read_bytes()

    def audit_trail(self, session_id: str, token: str | None) -> list[dict]:
        session = self.authorize(session_id, token)
        return self.repo.events(session.id)

    # ------------------------------------------------------------------ retention
    def delete_session(self, session_id: str, token: str | None) -> None:
        session = self.authorize(session_id, token)
        self.store.delete_session(session.id)
        self.repo.delete_session(session.id)
        logger.info("session_deleted", extra={"session_id": session.id})

    def purge_expired(self) -> int:
        ids = self.repo.expired_session_ids()
        for session_id in ids:
            self.store.delete_session(session_id)
            self.repo.delete_session(session_id)
        if ids:
            logger.info("sessions_purged", extra={"count": len(ids)})
        return len(ids)
