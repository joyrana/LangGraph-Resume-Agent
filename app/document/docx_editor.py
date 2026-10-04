"""Deterministic, minimal, transactional DOCX text editing.

Guarantees (each enforced by code and re-checked by the fidelity evaluator):

* Only the ``text`` of existing ``w:t`` elements inside targeted paragraphs
  changes (plus ``xml:space="preserve"`` where whitespace requires it). No run,
  paragraph, property, relationship or part is added or removed.
* Unchanged words keep their original run: a token-level diff between the
  original range and the replacement decides which characters are kept, and
  new characters inherit the formatting of the text they replace (or of the
  preceding character for pure insertions).
* Every operation verifies the source document version, the paragraph text
  hash and the exact original text immediately before editing.
* All operations succeed or none are applied (the caller receives no output).
* Parts that are not edited are copied byte-for-byte from the original.
"""

from __future__ import annotations

import difflib
import re
from collections import defaultdict
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.document.document_models import DocumentIndex
from app.document.docx_parser import DocxPackage, ParagraphModel, SegmentKind, parse_docx, serialize_xml, text_hash
from app.document.location_index import LocationResolver, RangeError, TextRange, check_range_editable
from app.document.ooxml import XML_NS

_TOKEN_RE = re.compile(r"\w+|\s+|[^\w\s]", re.UNICODE)
_FORBIDDEN_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\t\n\r  ￾￿]")


class EditOperation(BaseModel):
    edit_id: str
    location_id: str
    start: int
    end: int
    expected_text: str
    new_text: str


class AppliedEdit(BaseModel):
    edit_id: str
    location_id: str
    part: str
    path: str
    paragraph_before: str
    paragraph_after: str


class EditFailure(BaseModel):
    edit_id: str
    code: str
    message: str


@dataclass
class EditResult:
    ok: bool
    output: bytes | None = None
    applied: list[AppliedEdit] = field(default_factory=list)
    failures: list[EditFailure] = field(default_factory=list)


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def plan_replacement(old: str, owners: list[int], new: str) -> list[tuple[str, int]]:
    """Return the new characters for a range, each tagged with the owning segment index."""
    old_tokens, new_tokens = _tokens(old), _tokens(new)
    offsets = [0]
    for tok in old_tokens:
        offsets.append(offsets[-1] + len(tok))
    out: list[tuple[str, int]] = []
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        c1, c2 = offsets[i1], offsets[i2]
        new_chunk = "".join(new_tokens[j1:j2])
        if tag == "equal":
            out.extend((old[k], owners[k]) for k in range(c1, c2))
        elif tag == "replace":
            owner = owners[c1]
            out.extend((ch, owner) for ch in new_chunk)
        elif tag == "insert":
            if out:
                owner = out[-1][1]
            elif c1 < len(owners):
                owner = owners[c1]
            else:
                owner = owners[-1]
            out.extend((ch, owner) for ch in new_chunk)
        # delete: nothing emitted
    return out


def _set_text(el, value: str) -> None:
    el.text = value
    needs_preserve = value != value.strip() or "  " in value
    if needs_preserve:
        el.set(f"{{{XML_NS}}}space", "preserve")


def apply_to_paragraph(model: ParagraphModel, rng: TextRange, new_text: str) -> None:
    """Mutate ``w:t`` elements of a paragraph in place for one validated range."""
    text_segments = [i for i, s in enumerate(model.segments) if s.kind == SegmentKind.TEXT and s.end > s.start]
    touched = [i for i in text_segments if model.segments[i].start < rng.end and model.segments[i].end > rng.start]
    owners: list[int] = []
    old_chars: list[str] = []
    for idx in touched:
        seg = model.segments[idx]
        lo, hi = max(seg.start, rng.start), min(seg.end, rng.end)
        for pos in range(lo, hi):
            owners.append(idx)
            old_chars.append(seg.text[pos - seg.start])
    planned = plan_replacement("".join(old_chars), owners, new_text)
    assigned: dict[int, list[str]] = defaultdict(list)
    last_owner = -1
    for ch, owner in planned:
        if owner < last_owner:  # pragma: no cover - guarded by construction
            raise RangeError("non_monotonic_plan", "Internal edit plan error.")
        last_owner = owner
        assigned[owner].append(ch)
    for idx in touched:
        seg = model.segments[idx]
        prefix = seg.text[: max(0, rng.start - seg.start)]
        suffix = seg.text[rng.end - seg.start :] if seg.end > rng.end else ""
        _set_text(seg.element, prefix + "".join(assigned.get(idx, [])) + suffix)


def apply_edits(original: bytes, index: DocumentIndex, operations: list[EditOperation]) -> EditResult:
    pkg = DocxPackage(original)
    if pkg.version != index.document_version:
        return EditResult(
            ok=False,
            failures=[EditFailure(edit_id="*", code="stale_source", message="The source document does not match the reviewed version.")],
        )
    failures: list[EditFailure] = []
    by_location: dict[str, list[EditOperation]] = defaultdict(list)
    seen_ids: set[str] = set()
    for op in operations:
        if op.edit_id in seen_ids:
            failures.append(EditFailure(edit_id=op.edit_id, code="duplicate_edit", message="The edit was submitted twice."))
            continue
        seen_ids.add(op.edit_id)
        if _FORBIDDEN_CHARS.search(op.new_text):
            failures.append(
                EditFailure(
                    edit_id=op.edit_id,
                    code="invalid_characters",
                    message="Replacement text contains line breaks, tabs or control characters.",
                )
            )
            continue
        by_location[op.location_id].append(op)

    resolver = LocationResolver(pkg)
    plans: list[tuple[str, str, ParagraphModel, list[EditOperation], str]] = []
    for location_id in sorted(by_location):
        ops = sorted(by_location[location_id], key=lambda o: (o.start, o.end, o.edit_id))
        block = index.block(location_id)
        if block is None:
            failures.extend(
                EditFailure(
                    edit_id=o.edit_id, code="unknown_location", message="The target location does not exist in this document version."
                )
                for o in ops
            )
            continue
        if not block.editable:
            failures.extend(
                EditFailure(edit_id=o.edit_id, code="read_only_location", message="The target location is read-only.") for o in ops
            )
            continue
        model = resolver.paragraph(block.part, block.path)
        if model is None or text_hash(model.text) != block.text_hash:
            failures.extend(
                EditFailure(edit_id=o.edit_id, code="stale_location", message="The target paragraph changed since it was indexed.")
                for o in ops
            )
            continue
        for prev, cur in zip(ops, ops[1:], strict=False):
            if cur.start < prev.end:
                failures.append(
                    EditFailure(
                        edit_id=cur.edit_id, code="overlapping_edits", message=f"Overlaps edit {prev.edit_id} in the same paragraph."
                    )
                )
        for op in ops:
            if model.text[op.start : op.end] != op.expected_text:
                failures.append(
                    EditFailure(edit_id=op.edit_id, code="text_mismatch", message="The original text at the target range does not match.")
                )
                continue
            try:
                check_range_editable(model, TextRange(op.start, op.end))
            except RangeError as exc:
                failures.append(EditFailure(edit_id=op.edit_id, code=exc.code, message=exc.message))
        expected = model.text
        for op in sorted(ops, key=lambda o: o.start, reverse=True):
            expected = expected[: op.start] + op.new_text + expected[op.end :]
        plans.append((block.part, block.path, model, ops, expected))

    if failures:
        return EditResult(ok=False, failures=failures)

    applied: list[AppliedEdit] = []
    touched_parts: set[str] = set()
    for part, path, model, ops, expected in plans:
        before = model.text
        for op in sorted(ops, key=lambda o: o.start, reverse=True):
            current = resolver.paragraph(part, path)
            assert current is not None
            try:
                check_range_editable(current, TextRange(op.start, op.end))
            except RangeError as exc:
                return EditResult(ok=False, failures=[EditFailure(edit_id=op.edit_id, code=exc.code, message=exc.message)])
            apply_to_paragraph(current, TextRange(op.start, op.end), op.new_text)
        final = resolver.paragraph(part, path)
        if final is None or final.text != expected:
            return EditResult(
                ok=False,
                failures=[
                    EditFailure(
                        edit_id=ops[0].edit_id,
                        code="verification_failed",
                        message="The paragraph text after editing did not match the approved result.",
                    )
                ],
            )
        touched_parts.add(part)
        for op in ops:
            applied.append(
                AppliedEdit(
                    edit_id=op.edit_id,
                    location_id=op.location_id,
                    part=part,
                    path=path,
                    paragraph_before=before,
                    paragraph_after=final.text,
                )
            )

    replaced = {part: serialize_xml(pkg.xml(part)) for part in sorted(touched_parts)}
    output = pkg.write(replaced)

    # Reopen check: the output must parse with our parser and with python-docx.
    try:
        parse_docx(output)
        import io

        from docx import Document

        Document(io.BytesIO(output))
    except Exception as exc:  # pragma: no cover - defensive
        return EditResult(
            ok=False,
            failures=[
                EditFailure(edit_id="*", code="output_unreadable", message=f"Edited document could not be reopened ({type(exc).__name__}).")
            ],
        )

    applied.sort(key=lambda a: a.edit_id)
    return EditResult(ok=True, output=output, applied=applied)
