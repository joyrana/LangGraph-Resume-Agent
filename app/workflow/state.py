"""Typed graph state. Holds identifiers and small metadata only, never document bytes."""

from __future__ import annotations

from typing import Any, TypedDict


class ProposalState(TypedDict, total=False):
    session_id: str
    document_version: str
    # outputs
    proposal_count: int
    rejected_count: int
    error_code: str
    error_message: str
    outcome: str  # "awaiting_review" | "analysis_failed"


class FinalizeState(TypedDict, total=False):
    session_id: str
    review_revision: int
    idempotency_key: str
    accepted_edit_ids: list[str]
    rejected_edit_ids: list[str]
    # outputs
    output_artifact_id: str
    report_artifact_id: str
    edit_failures: list[dict[str, Any]]
    decision: str
    finalization_id: str
