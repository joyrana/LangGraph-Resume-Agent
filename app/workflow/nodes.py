"""Graph nodes. Each node is small, receives explicit dependencies and returns a partial state update."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import LLMError, StaleReview
from app.document.document_models import DocumentIndex
from app.document.docx_editor import EditOperation, apply_edits
from app.document.docx_parser import DocxPackage
from app.document.fidelity_evaluator import FidelityEvaluator
from app.document.fidelity_models import EditSpec, GateStatus
from app.llm.provider import LLMProvider
from app.persistence.artifact_store import ArtifactStore
from app.persistence.repositories import Repository
from app.resume.edit_proposer import propose_edits
from app.resume.jd_analyzer import coverage_estimate
from app.resume.proposal_models import Decision
from app.workflow.state import FinalizeState, ProposalState

logger = logging.getLogger(__name__)


@dataclass
class WorkflowDeps:
    settings: Settings
    repo: Repository
    store: ArtifactStore
    provider: LLMProvider
    evaluator: FidelityEvaluator


def load_source(deps: WorkflowDeps, session_id: str) -> tuple[bytes, DocumentIndex]:
    original = deps.store.get(deps.repo.find_artifact(session_id, "original"))
    index = DocumentIndex.model_validate_json(deps.store.get(deps.repo.find_artifact(session_id, "index")))
    return original, index


# ---------------------------------------------------------------- proposal graph


async def request_proposals(state: ProposalState, deps: WorkflowDeps) -> ProposalState:
    session = deps.repo.get_session(state["session_id"])
    original, index = load_source(deps, session.id)
    if index.document_version != session.document_version:
        return {"error_code": "stale_source", "error_message": "The stored document does not match the session."}
    s = deps.settings
    try:
        run = await propose_edits(
            provider=deps.provider,
            index=index,
            pkg=DocxPackage(original),
            job_description=session.job_description,
            company_details=session.company_details,
            candidate_notes=session.candidate_notes,
            char_budget=s.llm_context_char_budget,
            max_proposals=s.max_proposals,
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
        )
    except LLMError as exc:
        return {"error_code": exc.code, "error_message": exc.message}
    estimate = coverage_estimate(session.job_description, index.full_text())
    analysis = {
        "alignment_summary": run.alignment_summary,
        "gaps_without_evidence": run.gaps_without_evidence,
        "alignment_estimate": estimate.model_dump(),
        "rejected_proposals": [r.model_dump() for r in run.rejected],
        "context_truncated": run.context_truncated,
        "model": run.model_metadata,
        "limitations": index.limitations,
    }
    deps.repo.complete_analysis(session.id, analysis, run.proposals)
    logger.info(
        "proposals_ready",
        extra={
            "session_id": session.id,
            "valid": len(run.proposals),
            "rejected": len(run.rejected),
            **{k: run.model_metadata.get(k) for k in ("model", "prompt_version", "latency_ms")},
        },
    )
    return {"proposal_count": len(run.proposals), "rejected_count": len(run.rejected), "outcome": "awaiting_review"}


async def record_analysis_failure(state: ProposalState, deps: WorkflowDeps) -> ProposalState:
    deps.repo.fail_analysis(state["session_id"], state.get("error_code", "analysis_failed"), state.get("error_message", "Analysis failed."))
    return {"outcome": "analysis_failed"}


def route_after_proposals(state: ProposalState) -> str:
    return "failed" if state.get("error_code") else "done"


# ---------------------------------------------------------------- finalize graph


def _specs(deps: WorkflowDeps, session_id: str, edit_ids: list[str]) -> list[EditSpec]:
    by_id = {r.proposal.edit_id: r.proposal for r in deps.repo.list_proposals(session_id)}
    return [
        EditSpec(
            edit_id=p.edit_id, location_id=p.location_id, start=p.start, end=p.end, original_text=p.original_text, new_text=p.proposed_text
        )
        for p in (by_id[i] for i in edit_ids)
    ]


async def load_review(state: FinalizeState, deps: WorkflowDeps) -> FinalizeState:
    records = deps.repo.list_proposals(state["session_id"])
    accepted = sorted(r.proposal.edit_id for r in records if r.decision == Decision.ACCEPTED)
    rejected = sorted(r.proposal.edit_id for r in records if r.decision != Decision.ACCEPTED)
    if accepted != sorted(state["accepted_edit_ids"]):
        # Decisions changed after the finalize request computed its key. Abort (do not record a
        # FAIL against this key, which would permanently block an otherwise valid edit set).
        raise StaleReview("Review decisions changed during finalization. Reload and try again.")
    return {"rejected_edit_ids": rejected}


def apply_accepted_edits(state: FinalizeState, deps: WorkflowDeps) -> FinalizeState:
    session_id = state["session_id"]
    original, index = load_source(deps, session_id)
    specs = _specs(deps, session_id, state["accepted_edit_ids"])
    ops = [
        EditOperation(
            edit_id=s.edit_id, location_id=s.location_id, start=s.start, end=s.end, expected_text=s.original_text, new_text=s.new_text
        )
        for s in specs
    ]
    result = apply_edits(original, index, ops)
    if not result.ok or result.output is None:
        return {"edit_failures": [f.model_dump() for f in result.failures]}
    artifact = deps.store.put(session_id, "output", result.output)
    deps.repo.add_artifact(artifact)
    return {"output_artifact_id": artifact.artifact_id}


def evaluate_fidelity(state: FinalizeState, deps: WorkflowDeps) -> FinalizeState:
    session_id = state["session_id"]
    session = deps.repo.get_session(session_id)
    original, index = load_source(deps, session_id)
    output = deps.store.get(deps.repo.get_artifact(session_id, state["output_artifact_id"]))
    accepted = _specs(deps, session_id, state["accepted_edit_ids"])
    rejected = _specs(deps, session_id, state.get("rejected_edit_ids", []))
    work = deps.store.work_dir(session_id, "fid_" + state["idempotency_key"][:16])
    report = deps.evaluator.evaluate(original, output, index, accepted, rejected, user_fact_text=session.candidate_notes, artifact_dir=work)
    stored = deps.store.put(session_id, "fidelity_report", report.model_dump_json(indent=2).encode())
    deps.repo.add_artifact(stored)
    return {"report_artifact_id": stored.artifact_id, "decision": report.decision.value}


def record_finalization(state: FinalizeState, deps: WorkflowDeps) -> FinalizeState:
    failures = state.get("edit_failures") or []
    decision = state.get("decision") or GateStatus.FAIL.value
    summary = {"edit_failures": failures} if failures else {}
    record = deps.repo.complete_finalization(
        state["session_id"],
        key=state["idempotency_key"],
        review_revision=state["review_revision"],
        decision=GateStatus.FAIL.value if failures else decision,
        output_artifact_id=None if failures else state.get("output_artifact_id"),
        report_artifact_id=state.get("report_artifact_id"),
        summary=summary,
        applied_edit_ids=[] if failures else state["accepted_edit_ids"],
    )
    return {"finalization_id": record.id, "decision": record.decision}


def route_after_review(state: FinalizeState) -> str:
    return "failed" if state.get("edit_failures") else "apply"


def route_after_apply(state: FinalizeState) -> str:
    return "failed" if state.get("edit_failures") else "evaluate"
