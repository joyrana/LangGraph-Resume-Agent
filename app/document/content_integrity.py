"""Gate C: content integrity against the exact approved edit set."""

from __future__ import annotations

from collections import Counter

from app.document.document_models import DocumentIndex
from app.document.fidelity_models import AcceptedEditCheck, EditSpec, Finding, GateResult, Severity
from app.resume.claims import extract_numeric_claims


def check_content(
    original_index: DocumentIndex,
    output_index: DocumentIndex,
    accepted: list[EditSpec],
    rejected: list[EditSpec],
    user_fact_text: str = "",
) -> tuple[GateResult, list[AcceptedEditCheck]]:
    findings: list[Finding] = []
    checks: list[AcceptedEditCheck] = []

    if [(b.part, b.path) for b in original_index.blocks] != [(b.part, b.path) for b in output_index.blocks]:
        findings.append(
            Finding(
                code="block_alignment_failed",
                severity=Severity.ERROR,
                message="Paragraph order or count changed; content cannot be verified.",
            )
        )
        return GateResult.from_findings("content", findings), [AcceptedEditCheck(edit_id=e.edit_id, status="missing") for e in accepted]

    by_loc: dict[str, list[EditSpec]] = {}
    for edit in accepted:
        by_loc.setdefault(edit.location_id, []).append(edit)

    expected_text: dict[str, str] = {}
    for blk in original_index.blocks:
        text = blk.text
        for edit in sorted(by_loc.get(blk.location_id, []), key=lambda e: e.start, reverse=True):
            if text[edit.start : edit.end] != edit.original_text:
                findings.append(
                    Finding(
                        code="accepted_edit_stale",
                        severity=Severity.ERROR,
                        message="An approved edit no longer matches the source text.",
                        details={"edit_id": edit.edit_id},
                    )
                )
            text = text[: edit.start] + edit.new_text + text[edit.end :]
        expected_text[blk.location_id] = text

    unintended = 0
    for a, b in zip(original_index.blocks, output_index.blocks, strict=True):
        exp = expected_text[a.location_id]
        edits_here = by_loc.get(a.location_id, [])
        if b.text == exp:
            for e in edits_here:
                checks.append(AcceptedEditCheck(edit_id=e.edit_id, status="applied_as_approved"))
            continue
        if edits_here:
            for e in edits_here:
                checks.append(
                    AcceptedEditCheck(edit_id=e.edit_id, status="mismatch", message="Paragraph text differs from the approved result.")
                )
            findings.append(
                Finding(
                    code="accepted_edit_mismatch",
                    severity=Severity.ERROR,
                    message="An edited paragraph does not match the approved text.",
                    details={"path": a.path, "part": a.part},
                )
            )
        else:
            unintended += 1
            findings.append(
                Finding(
                    code="unintended_text_change",
                    severity=Severity.ERROR,
                    message="Text changed in a paragraph without an approved edit.",
                    details={"path": a.path, "part": a.part},
                )
            )

    accepted_ids = {e.edit_id for e in accepted}
    for e in rejected:
        if e.edit_id in accepted_ids:
            findings.append(
                Finding(
                    code="edit_both_accepted_and_rejected",
                    severity=Severity.ERROR,
                    message="An edit is recorded as both accepted and rejected.",
                    details={"edit_id": e.edit_id},
                )
            )
            continue
        out_blk = next((b for a, b in zip(original_index.blocks, output_index.blocks, strict=True) if a.location_id == e.location_id), None)
        if out_blk is not None and e.new_text and e.new_text in out_blk.text and e.new_text not in expected_text[e.location_id]:
            findings.append(
                Finding(
                    code="rejected_edit_present",
                    severity=Severity.ERROR,
                    message="A rejected edit appears in the output.",
                    details={"edit_id": e.edit_id},
                )
            )

    before_counts = Counter(b.text for b in original_index.blocks if b.text.strip())
    after_counts = Counter(b.text for b in output_index.blocks if b.text.strip())
    duplicated = [t for t, n in after_counts.items() if n > max(1, before_counts.get(t, 0))]
    if duplicated:
        findings.append(
            Finding(
                code="duplicate_content",
                severity=Severity.ERROR,
                message=f"{len(duplicated)} paragraph(s) appear more often than in the original.",
            )
        )

    evidence_pool = original_index.full_text() + "\n" + user_fact_text
    known_claims = extract_numeric_claims(evidence_pool)
    for e in accepted:
        new_claims = extract_numeric_claims(e.new_text) - known_claims
        if new_claims:
            findings.append(
                Finding(
                    code="unsupported_numeric_claim",
                    severity=Severity.ERROR,
                    message="An approved edit introduces a number not found in the resume or user-provided facts.",
                    details={"edit_id": e.edit_id, "claims": sorted(new_claims)},
                )
            )

    for e in accepted:
        if not any(c.edit_id == e.edit_id for c in checks):
            checks.append(AcceptedEditCheck(edit_id=e.edit_id, status="missing", message="Target location not found."))

    return GateResult.from_findings(
        "content",
        findings,
        {"accepted_edits": len(accepted), "rejected_edits": len(rejected), "unintended_text_changes": unintended},
    ), sorted(checks, key=lambda c: c.edit_id)
