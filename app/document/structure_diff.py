"""Gate B: structural preservation.

Two independent comparisons:

1. **Package/XML level.** Every ZIP member other than the parts containing
   edited paragraphs must be byte-identical. Within edited parts, the XML must
   be identical after blanking the text of ``w:t`` elements in the edited
   paragraphs (and their ``xml:space`` attribute). Any other difference is an
   unexpected structural change.
2. **Semantic level.** Both documents are re-parsed and compared on section
   page settings, columns, feature counts (tables, images, hyperlinks, fields,
   headers, footers...), and per-paragraph style, heading level, list level,
   container, table position and run formatting signatures.

The second comparison does not rely on how the editor works, so a bug in the
editor that slips past the first check is still caught here.
"""

from __future__ import annotations

import copy

from lxml import etree

from app.document.document_models import DocumentIndex
from app.document.docx_parser import DocxPackage, element_path, iter_paragraphs, parse_docx
from app.document.fidelity_models import Finding, GateResult, Severity
from app.document.ooxml import XML_NS, w

_SPACE_ATTR = f"{{{XML_NS}}}space"


def _blank_paragraph_text(p: etree._Element) -> None:
    for t in p.iter(w("t")):
        t.text = ""
        t.attrib.pop(_SPACE_ATTR, None)


def _canonical(el: etree._Element) -> bytes:
    return etree.tostring(el, method="c14n")


def _paragraph_map(root: etree._Element) -> dict[str, etree._Element]:
    return {element_path(p): p for p, _, _ in iter_paragraphs(root)}


def compare_structure(
    original: bytes,
    output: bytes,
    original_index: DocumentIndex,
    edited_location_ids: set[str],
) -> GateResult:
    findings: list[Finding] = []
    src = DocxPackage(original)
    dst = DocxPackage(output)

    edited = [b for b in original_index.blocks if b.location_id in edited_location_ids]
    edited_paths: dict[str, set[str]] = {}
    for blk in edited:
        edited_paths.setdefault(blk.part, set()).add(blk.path)

    # ---- 1. package members
    src_names, dst_names = set(src.members), set(dst.members)
    for name in sorted(dst_names - src_names):
        findings.append(Finding(code="part_added", severity=Severity.ERROR, message=f"Package part added: {name}", details={"part": name}))
    for name in sorted(src_names - dst_names):
        findings.append(
            Finding(code="part_removed", severity=Severity.ERROR, message=f"Package part removed: {name}", details={"part": name})
        )

    changed_parts: list[str] = []
    for name in sorted(src_names & dst_names):
        if src.members[name] == dst.members[name]:
            continue
        changed_parts.append(name)
        if name not in edited_paths:
            findings.append(
                Finding(
                    code="unexpected_part_change",
                    severity=Severity.ERROR,
                    message=f"Part changed without an approved edit: {name}",
                    details={"part": name},
                )
            )
            continue
        a_root = copy.deepcopy(src.xml(name))
        b_root = copy.deepcopy(dst.xml(name))
        a_pars, b_pars = _paragraph_map(a_root), _paragraph_map(b_root)
        if list(a_pars) != list(b_pars):
            findings.append(
                Finding(
                    code="paragraph_structure_changed",
                    severity=Severity.ERROR,
                    message=f"Paragraph structure changed in {name}",
                    details={"part": name},
                )
            )
            continue
        changed_paths: list[str] = []
        for path, a_p in a_pars.items():
            b_p = b_pars[path]
            if path in edited_paths[name]:
                _blank_paragraph_text(a_p)
                _blank_paragraph_text(b_p)
                if _canonical(a_p) != _canonical(b_p):
                    findings.append(
                        Finding(
                            code="edited_paragraph_markup_changed",
                            severity=Severity.ERROR,
                            message="Formatting or run structure changed in an edited paragraph.",
                            details={"part": name, "path": path},
                        )
                    )
            elif _canonical(a_p) != _canonical(b_p):
                changed_paths.append(path)
        for path in changed_paths:
            findings.append(
                Finding(
                    code="unedited_paragraph_changed",
                    severity=Severity.ERROR,
                    message="A paragraph without an approved edit changed.",
                    details={"part": name, "path": path},
                )
            )
        if not changed_paths and _canonical(a_root) != _canonical(b_root):
            findings.append(
                Finding(
                    code="non_paragraph_markup_changed",
                    severity=Severity.ERROR,
                    message=f"Non-paragraph markup (tables, sections, properties) changed in {name}.",
                    details={"part": name},
                )
            )

    # ---- 2. semantic comparison (independent re-parse)
    try:
        out_index, _ = parse_docx(output)
    except Exception as exc:  # pragma: no cover - integrity gate normally catches this first
        findings.append(
            Finding(code="output_unparseable", severity=Severity.ERROR, message=f"Output could not be parsed ({type(exc).__name__}).")
        )
        return GateResult.from_findings("structure", findings, {"changed_parts": changed_parts})

    a_secs = [s.model_dump() for s in original_index.sections]
    b_secs = [s.model_dump() for s in out_index.sections]
    if a_secs != b_secs:
        findings.append(
            Finding(
                code="section_properties_changed",
                severity=Severity.ERROR,
                message="Section, page size, margin or column settings changed.",
                details={"before": a_secs, "after": b_secs},
            )
        )

    a_feat, b_feat = original_index.features.model_dump(), out_index.features.model_dump()
    for key in sorted(a_feat):
        if a_feat[key] != b_feat.get(key):
            findings.append(
                Finding(
                    code="feature_count_changed",
                    severity=Severity.ERROR,
                    message=f"Document feature count changed: {key}",
                    details={"feature": key, "before": a_feat[key], "after": b_feat.get(key)},
                )
            )

    if len(original_index.blocks) != len(out_index.blocks):
        findings.append(Finding(code="paragraph_count_changed", severity=Severity.ERROR, message="The number of paragraphs changed."))
    else:
        for a, b in zip(original_index.blocks, out_index.blocks, strict=True):
            props = (
                "part",
                "path",
                "container",
                "style_id",
                "heading_level",
                "is_list_item",
                "list_level",
                "run_count",
                "distinct_run_formats",
                "has_hyperlink",
                "has_field",
            )
            diffs = {p: (getattr(a, p), getattr(b, p)) for p in props if getattr(a, p) != getattr(b, p)}
            if a.table_cell != b.table_cell:
                diffs["table_cell"] = (a.table_cell and a.table_cell.model_dump(), b.table_cell and b.table_cell.model_dump())
            if diffs:
                findings.append(
                    Finding(
                        code="paragraph_properties_changed",
                        severity=Severity.ERROR,
                        message="Paragraph style, list, table position or run formatting changed.",
                        details={"path": a.path, "part": a.part, "changes": {k: list(v) for k, v in diffs.items()}},
                    )
                )

    return GateResult.from_findings(
        "structure",
        findings,
        {
            "changed_parts": changed_parts,
            "expected_changed_parts": sorted(edited_paths),
            "paragraphs": len(out_index.blocks),
            "sections": len(out_index.sections),
        },
    )
