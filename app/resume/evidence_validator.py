"""Deterministic validation of model proposals against the source document.

Nothing the model says is trusted: target, quote, range, evidence, numbers,
named terms and claim strength are all re-derived from the indexed document.
A proposal that fails any hard check is rejected and never shown as an edit
the user can accept.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.document.document_models import DocumentIndex
from app.document.docx_parser import DocxPackage
from app.document.location_index import LocationResolver, RangeError, TextRange, check_range_editable, locate_unique
from app.resume import edit_policy
from app.resume.claims import extract_numeric_claims, named_terms, normalize_term
from app.resume.jd_analyzer import salient_terms
from app.resume.proposal_models import (
    EditProposal,
    EditType,
    Evidence,
    LLMProposal,
    LLMProposalBatch,
    RejectedProposal,
    RiskLevel,
)

_FORBIDDEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\t\n\r  ]")


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def idempotency_key(document_version: str, location_id: str, start: int, end: int, proposed: str) -> str:
    raw = f"{document_version}|{location_id}|{start}|{end}|{proposed}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _Reject(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


@dataclass
class ValidationOutcome:
    accepted: list[EditProposal]
    rejected: list[RejectedProposal]


def _term_keys(text: str) -> set[str]:
    return {normalize_term(t) for t in named_terms(text)} - {""}


def _token_keys(text: str) -> set[str]:
    return {normalize_term(t) for t in re.findall(r"\S+", text)} - {""}


def _compound_parts(text: str) -> dict[str, list[str]]:
    """Map normalised hyphen/slash compounds to their raw parts."""
    parts: dict[str, list[str]] = {}
    for raw in re.findall(r"\S+", text):
        raw = raw.strip("()[]{}\"'`,;:!?.")
        if re.search(r"[A-Za-z0-9][-/][A-Za-z0-9]", raw):
            pieces = [p for p in re.split(r"[-/]", raw) if normalize_term(p)]
            if len(pieces) > 1:
                parts[normalize_term(raw)] = pieces
    return parts


def _looks_named(raw: str) -> bool:
    return any(ch.isdigit() for ch in raw) or any(ch.isupper() for ch in raw) or bool(re.search(r"[+#]", raw))


class EvidenceValidator:
    def __init__(self, index: DocumentIndex, pkg: DocxPackage, job_description: str, candidate_notes: str, max_proposals: int) -> None:
        if pkg.version != index.document_version:
            raise ValueError("Package and index versions differ")
        self.index = index
        self.resolver = LocationResolver(pkg)
        self.candidate_notes = candidate_notes or ""
        self.max_proposals = max_proposals
        self.resume_text = index.full_text()
        self.resume_numbers = extract_numeric_claims(self.resume_text)
        self.resume_tokens = _token_keys(self.resume_text)
        self.notes_numbers = extract_numeric_claims(self.candidate_notes)
        self.notes_tokens = _token_keys(self.candidate_notes)
        self.jd_keys = {normalize_term(t) for t in salient_terms(job_description, limit=80)} | _term_keys(job_description)
        self.jd_keys.discard("")

    # ------------------------------------------------------------------ public
    def validate(self, batch: LLMProposalBatch) -> ValidationOutcome:
        accepted: list[EditProposal] = []
        rejected: list[RejectedProposal] = []
        seen_keys: set[str] = set()
        ranges: dict[str, list[tuple[int, int]]] = {}
        # Higher self-reported confidence first only to break ties between overlapping
        # proposals; it never bypasses a check.
        ordered = sorted(enumerate(batch.proposals), key=lambda item: (-item[1].confidence, item[0]))
        for _, raw in ordered:
            try:
                proposal = self._validate_one(raw)
                if proposal.idempotency_key in seen_keys:
                    raise _Reject("duplicate_proposal", "Duplicate of another proposal.")
                for s, e in ranges.get(proposal.location_id, []):
                    if proposal.start < e and s < proposal.end:
                        raise _Reject("overlapping_proposal", "Overlaps another proposal in the same paragraph.")
                if len(accepted) >= self.max_proposals:
                    raise _Reject("over_limit", f"More than {self.max_proposals} proposals.")
            except _Reject as exc:
                rejected.append(
                    RejectedProposal(
                        location_id=raw.location_id,
                        original_text=raw.original_text,
                        proposed_text=raw.proposed_text,
                        code=exc.code,
                        message=exc.message,
                    )
                )
                continue
            seen_keys.add(proposal.idempotency_key)
            ranges.setdefault(proposal.location_id, []).append((proposal.start, proposal.end))
            accepted.append(proposal)
        # Present in document order.
        order = {b.location_id: i for i, b in enumerate(self.index.blocks)}
        accepted.sort(key=lambda p: (order.get(p.location_id, 0), p.start))
        return ValidationOutcome(accepted=accepted, rejected=rejected)

    # ------------------------------------------------------------------ checks
    def _validate_one(self, raw: LLMProposal) -> EditProposal:
        block = self.index.block(raw.location_id)
        if block is None:
            raise _Reject("unknown_location", "The proposal references a location that does not exist in this document version.")
        if not block.editable:
            raise _Reject("read_only_location", f"The target paragraph is read-only ({block.read_only_reason or 'unsupported content'}).")
        try:
            rng = locate_unique(block.text, raw.original_text)
            model = self.resolver.paragraph(block.part, block.path)
            if model is None or model.text != block.text:
                raise _Reject("stale_location", "The target paragraph does not match the indexed text.")
            check_range_editable(model, TextRange(rng.start, rng.end))
        except RangeError as exc:
            raise _Reject(exc.code, exc.message) from exc

        original, proposed = raw.original_text, raw.proposed_text
        if not proposed.strip():
            raise _Reject("empty_replacement", "Deleting text is not supported as a proposal.")
        if _FORBIDDEN.search(proposed):
            raise _Reject("invalid_characters", "The replacement contains line breaks, tabs or control characters.")
        if proposed == original:
            raise _Reject("no_change", "The replacement is identical to the original.")
        allowance = max(edit_policy.MIN_GROWTH_ALLOWANCE, int(len(original) * edit_policy.MAX_RELATIVE_GROWTH))
        if len(proposed) - len(original) > min(allowance, edit_policy.MAX_ABSOLUTE_GROWTH_CHARS):
            raise _Reject("too_long", "The replacement is much longer than the original; edits must stay small.")
        if edit_policy.URL_OR_EMAIL.search(proposed) and not edit_policy.URL_OR_EMAIL.search(original):
            raise _Reject("unsupported_contact_or_link", "The replacement introduces a link, domain or e-mail address.")

        # Evidence quotes must exist verbatim (whitespace-normalised) at the cited location.
        evidence: list[Evidence] = []
        for ev in raw.supporting_evidence:
            ev_block = self.index.block(ev.location_id)
            if ev_block is None or _norm_ws(ev.quote) not in _norm_ws(ev_block.text):
                raise _Reject("evidence_not_found", "Supporting evidence is not quoted verbatim from the resume.")
            evidence.append(Evidence(location_id=ev.location_id, quote=ev.quote, section=ev_block.section))
        evidence_text = " ".join(e.quote for e in evidence)

        warnings: list[str] = []
        risk = RiskLevel.LOW if raw.edit_type in (EditType.GRAMMAR, EditType.CLARITY, EditType.CONCISION) else RiskLevel.MEDIUM

        # Numbers: every new number must be in cited evidence or user-provided facts.
        original_numbers = extract_numeric_claims(original)
        new_numbers = extract_numeric_claims(proposed) - original_numbers
        removed_numbers = original_numbers - extract_numeric_claims(proposed)
        cited_numbers = extract_numeric_claims(evidence_text) | self.notes_numbers
        if removed_numbers and (new_numbers - cited_numbers):
            # Replacing a stated number with a different one alters a fact, even if the new
            # value appears elsewhere in the resume ("two engineers" -> "five engineers").
            raise _Reject("changed_number", "The replacement changes a number stated in the resume.")
        for claim in sorted(new_numbers):
            if claim in cited_numbers:
                continue
            if claim in self.resume_numbers:
                warnings.append(f"'{claim}' appears elsewhere in the resume but not in the cited evidence; confirm it applies here.")
                risk = max(risk, RiskLevel.MEDIUM, key=_risk_order)
                continue
            raise _Reject("unsupported_number", f"The replacement introduces '{claim}', which is not in the resume or your provided facts.")

        # Named terms and job-description keywords.
        original_tokens = _token_keys(original)
        evidence_tokens = _token_keys(evidence_text)
        new_named = _term_keys(proposed) - original_tokens
        new_tokens = _token_keys(proposed) - original_tokens
        jd_introduced = {t for t in new_tokens if t in self.jd_keys}
        compounds = _compound_parts(proposed)
        candidates = {k for k in new_named | jd_introduced if k not in compounds}
        for compound_key, raw_parts in compounds.items():
            if compound_key in original_tokens:
                continue
            # A compound ("2M-request", "Kubernetes-based") is judged by its parts:
            # named-looking parts and job-description keywords need support; generic words do not.
            for raw_part in raw_parts:
                part = normalize_term(raw_part)
                if part not in original_tokens and (part in self.jd_keys or _looks_named(raw_part)):
                    candidates.add(part)
        for key in sorted(candidates):
            if key in evidence_tokens or key in self.notes_tokens:
                continue
            if key in self.resume_tokens:
                warnings.append(f"'{key}' is supported elsewhere in the resume but not by the cited evidence.")
                risk = max(risk, RiskLevel.MEDIUM, key=_risk_order)
                continue
            if key in self.jd_keys:
                raise _Reject("jd_keyword_without_evidence", f"'{key}' comes from the job description but the resume does not show it.")
            raise _Reject("unsupported_term", f"'{key}' does not appear in the resume or your provided facts.")

        # Claim strength.
        support = evidence_text + " " + self.candidate_notes
        outcome = edit_policy.introduced_verbs(original, proposed, support, edit_policy.OUTCOME_VERBS)
        leadership = edit_policy.introduced_verbs(original, proposed, support, edit_policy.LEADERSHIP_VERBS)
        if outcome:
            warnings.append(f"Introduces an outcome claim ({', '.join(outcome)}) not stated in the evidence. Confirm it is true.")
            risk = RiskLevel.HIGH
        if leadership:
            warnings.append(
                f"Introduces a leadership/ownership claim ({', '.join(leadership)}) not stated in the evidence. Confirm it is true."
            )
            risk = RiskLevel.HIGH

        key = idempotency_key(self.index.document_version, block.location_id, rng.start, rng.end, proposed)
        return EditProposal(
            edit_id="edt_" + key[:16],
            idempotency_key=key,
            source_document_version=self.index.document_version,
            location_id=block.location_id,
            section=block.section,
            container=block.container.value,
            original_text=original,
            proposed_text=proposed,
            start=rng.start,
            end=rng.end,
            paragraph_text=block.text,
            edit_type=raw.edit_type,
            reason=raw.reason.strip(),
            supporting_evidence=evidence,
            model_confidence=raw.confidence,
            risk_level=risk,
            requires_user_confirmation=risk == RiskLevel.HIGH,
            validation_warnings=warnings,
        )


def _risk_order(level: RiskLevel) -> int:
    return {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}[level]
