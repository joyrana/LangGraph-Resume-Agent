"""Builders for model output used across tests (the model is mocked)."""

from __future__ import annotations

import json

from app.document.document_models import DocumentIndex


def block(index: DocumentIndex, needle: str):
    found = [b for b in index.blocks if needle in b.text]
    assert len(found) == 1, f"{needle!r} matched {len(found)} blocks"
    return found[0]


def proposal(
    index: DocumentIndex,
    needle: str,
    original: str,
    proposed: str,
    *,
    evidence: list[tuple[str, str]] | None = None,
    edit_type: str = "clarity",
    confidence: float = 0.8,
    reason: str = "Clearer wording.",
) -> dict:
    target = block(index, needle)
    if evidence is None:
        evidence = [(needle, original)]
    return {
        "location_id": target.location_id,
        "original_text": original,
        "proposed_text": proposed,
        "edit_type": edit_type,
        "reason": reason,
        "supporting_evidence": [{"location_id": block(index, n).location_id, "quote": q} for n, q in evidence],
        "confidence": confidence,
    }


def batch_json(*proposals: dict, summary: str = "Good match.", gaps: list[str] | None = None) -> str:
    return json.dumps({"alignment_summary": summary, "gaps_without_evidence": gaps or [], "proposals": list(proposals)})
