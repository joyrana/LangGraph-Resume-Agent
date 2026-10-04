"""Structured DOCX parsing with stable, version-bound source locations.

The parser reads OOXML directly (via the hardened parser in ``ooxml``) so the
same traversal can be replayed by the editor on a working copy. Each
paragraph is decomposed into *segments*:

* ``TEXT``   – the contents of one ``w:t`` element (editable unless inside a
  field result, tracked change, inline content control or text box);
* ``TAB`` / ``BREAK`` / ``SYMBOL`` – one-character atoms that are never edited;
* ``OBJECT`` / ``MARKER`` – zero-width atoms (drawings, math, bookmarks,
  comment anchors) that an edit range may touch but never cross.

A location id is ``loc_`` + sha256(document_version | part | path | text_hash),
so it is tied to the exact source document and paragraph contents; a stale
or modified document yields different ids.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from enum import Enum

from lxml import etree

from app.document.document_models import (
    Block,
    Container,
    DocumentIndex,
    FeatureInventory,
    SectionInfo,
    TableCellRef,
)
from app.document.ooxml import (
    A_NS,
    FOOTER_CT,
    HEADER_CT,
    M_NS,
    MC_NS,
    REL_OFFICE_DOCUMENT,
    REL_TYPE_PREFIX,
    W_NS,
    content_types,
    local_name,
    parse_relationships,
    parse_xml,
    rels_path_for,
    resolve_target,
    serialize_xml,
    w,
)


class SegmentKind(str, Enum):
    TEXT = "text"
    TAB = "tab"
    BREAK = "break"
    SYMBOL = "symbol"
    OBJECT = "object"
    MARKER = "marker"


@dataclass
class Segment:
    kind: SegmentKind
    text: str
    start: int
    end: int
    editable: bool
    reason: str | None = None
    element: etree._Element | None = None
    hyperlink_key: str | None = None


@dataclass
class ParagraphModel:
    element: etree._Element
    segments: list[Segment]

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments)


@dataclass
class _Ctx:
    hyperlink_key: str | None = None
    reason: str | None = None


@dataclass
class _FieldState:
    stack: list[str] = field(default_factory=list)  # "code" | "result"

    @property
    def in_code(self) -> bool:
        return bool(self.stack) and self.stack[-1] == "code"

    @property
    def in_result(self) -> bool:
        return "result" in self.stack


_TRANSPARENT = {"smartTag", "customXml", "dir", "bdo"}
_ZERO_WIDTH_MARKERS = {
    "bookmarkStart",
    "bookmarkEnd",
    "commentRangeStart",
    "commentRangeEnd",
    "permStart",
    "permEnd",
    "moveFromRangeStart",
    "moveFromRangeEnd",
    "moveToRangeStart",
    "moveToRangeEnd",
}
_SKIP = {"pPr", "rPr", "proofErr", "lastRenderedPageBreak", "softHyphen", "annotationRef"}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def document_version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- segments


def build_paragraph(p_el: etree._Element, base_reason: str | None = None) -> ParagraphModel:
    segments: list[Segment] = []
    pos = 0
    fields = _FieldState()

    def add(kind: SegmentKind, text: str, editable: bool, reason: str | None, el: etree._Element | None, ctx: _Ctx) -> None:
        nonlocal pos
        segments.append(Segment(kind, text, pos, pos + len(text), editable, reason, el, ctx.hyperlink_key))
        pos += len(text)

    def visit_run(run: etree._Element, ctx: _Ctx) -> None:
        for child in run:
            name = local_name(child.tag)
            if child.tag is etree.Comment or name in _SKIP:
                continue
            if name == "fldChar":
                kind = child.get(w("fldCharType"))
                if kind == "begin":
                    fields.stack.append("code")
                elif kind == "separate" and fields.stack:
                    fields.stack[-1] = "result"
                elif kind == "end" and fields.stack:
                    fields.stack.pop()
                add(SegmentKind.MARKER, "", False, "field", child, ctx)
                continue
            if fields.in_code:
                continue  # instrText and anything inside a field code is not visible
            if name == "t":
                reason = ctx.reason or ("field_result" if fields.in_result else None)
                add(SegmentKind.TEXT, child.text or "", reason is None, reason, child, ctx)
            elif name == "tab" or name == "ptab":
                add(SegmentKind.TAB, "\t", False, "tab", child, ctx)
            elif name in {"br", "cr"}:
                add(SegmentKind.BREAK, "\n", False, "break", child, ctx)
            elif name == "noBreakHyphen":
                add(SegmentKind.SYMBOL, "-", False, "symbol", child, ctx)
            elif name == "sym":
                add(SegmentKind.SYMBOL, "□", False, "symbol", child, ctx)
            elif name in {"delText", "instrText", "delInstrText"}:
                continue
            else:
                # drawing, pict, object, AlternateContent, footnoteReference, ...
                add(SegmentKind.OBJECT, "", False, f"object:{name}", child, ctx)

    def visit(el: etree._Element, ctx: _Ctx) -> None:
        for child in el:
            if child.tag is etree.Comment or child.tag is etree.ProcessingInstruction:
                continue
            ns = child.tag.split("}", 1)[0][1:] if isinstance(child.tag, str) and "}" in child.tag else ""
            name = local_name(child.tag)
            if ns == M_NS:
                add(SegmentKind.OBJECT, "", False, "math", child, ctx)
                continue
            if ns == MC_NS:
                add(SegmentKind.OBJECT, "", False, "alternate_content", child, ctx)
                continue
            if ns != W_NS:
                continue
            if name in _SKIP:
                continue
            if name == "r":
                visit_run(child, ctx)
            elif name == "hyperlink":
                key = child.get(f"{{{_R}}}id") or ("#" + (child.get(w("anchor")) or ""))
                visit(child, _Ctx(hyperlink_key=f"{key}@{id(child)}", reason=ctx.reason))
            elif name in {"ins", "moveTo"}:
                visit(child, _Ctx(ctx.hyperlink_key, ctx.reason or "tracked_change"))
            elif name in {"del", "moveFrom"}:
                visit(child, _Ctx(ctx.hyperlink_key, "tracked_change"))
            elif name == "fldSimple":
                add(SegmentKind.MARKER, "", False, "field", child, ctx)
                visit(child, _Ctx(ctx.hyperlink_key, ctx.reason or "field_result"))
                add(SegmentKind.MARKER, "", False, "field", child, ctx)
            elif name == "sdt":
                content = child.find(w("sdtContent"))
                if content is not None:
                    visit(content, _Ctx(ctx.hyperlink_key, ctx.reason or "inline_content_control"))
            elif name in _TRANSPARENT:
                visit(child, ctx)
            elif name in _ZERO_WIDTH_MARKERS:
                add(SegmentKind.MARKER, "", False, name, child, ctx)
            elif name in {"oMath", "oMathPara"}:
                add(SegmentKind.OBJECT, "", False, "math", child, ctx)
            else:
                if child.find(f".//{w('t')}") is not None:
                    add(SegmentKind.OBJECT, "", False, f"unsupported:{name}", child, ctx)

    visit(p_el, _Ctx(reason=base_reason))
    return ParagraphModel(p_el, segments)


_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


# --------------------------------------------------------------------------- package


class DocxPackage:
    """Read-only view of a DOCX ZIP with ordered members and lazily parsed XML."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.version = document_version(data)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.infos = zf.infolist()
            self.members: dict[str, bytes] = {i.filename: zf.read(i) for i in self.infos if not i.is_dir()}
        self._xml: dict[str, etree._Element] = {}
        ct_root = parse_xml(self.members["[Content_Types].xml"])
        self.overrides, self.defaults = content_types(ct_root)
        rels = parse_relationships(self.members["_rels/.rels"])
        main = [r["target"] for r in rels if r["type"] == REL_OFFICE_DOCUMENT]
        self.main_part = resolve_target("", main[0])

    def xml(self, part: str) -> etree._Element:
        if part not in self._xml:
            self._xml[part] = parse_xml(self.members[part])
        return self._xml[part]

    def content_type(self, part: str) -> str:
        return self.overrides.get(part) or self.defaults.get(part.rsplit(".", 1)[-1].lower(), "")

    def relationships(self, part: str) -> list[dict[str, str]]:
        rels_name = rels_path_for(part)
        if rels_name not in self.members:
            return []
        return parse_relationships(self.members[rels_name])

    def editable_parts(self) -> list[tuple[str, Container]]:
        parts: list[tuple[str, Container]] = [(self.main_part, Container.BODY)]
        for rel in self.relationships(self.main_part):
            if rel["mode"] == "External":
                continue
            target = resolve_target(self.main_part, rel["target"])
            ct = self.content_type(target)
            if ct == HEADER_CT and target in self.members:
                parts.append((target, Container.HEADER))
            elif ct == FOOTER_CT and target in self.members:
                parts.append((target, Container.FOOTER))
        # Deterministic order
        head = parts[:1]
        rest = sorted(parts[1:], key=lambda item: item[0])
        return head + rest

    def write(self, replaced_parts: dict[str, bytes]) -> bytes:
        """Write a new package: replaced parts get new bytes, all others are copied unchanged."""
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            for info in self.infos:
                if info.is_dir():
                    continue
                payload = replaced_parts.get(info.filename, self.members[info.filename])
                new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr
                new_info.create_system = info.create_system
                zf.writestr(new_info, payload)
        return out.getvalue()


# --------------------------------------------------------------------------- traversal


def element_path(el: etree._Element) -> str:
    parts: list[str] = []
    node = el
    while node is not None and node.getparent() is not None:
        parent = node.getparent()
        same = [c for c in parent if c.tag == node.tag]
        parts.append(f"{local_name(node.tag)}[{same.index(node)}]")
        node = parent
    return "/".join(reversed(parts))


def find_by_path(root: etree._Element, path: str) -> etree._Element | None:
    node = root
    for piece in path.split("/"):
        m = re.fullmatch(r"([A-Za-z0-9_]+)\[(\d+)\]", piece)
        if not m:
            return None
        name, idx = m.group(1), int(m.group(2))
        matches = [c for c in node if isinstance(c.tag, str) and local_name(c.tag) == name]
        if idx >= len(matches):
            return None
        node = matches[idx]
    return node


def iter_paragraphs(root: etree._Element):
    """Yield (paragraph element, base read-only reason, container override) in document order."""
    for p in root.iter(w("p")):
        reason: str | None = None
        container: Container | None = None
        skip = False
        for anc in p.iterancestors():
            name = local_name(anc.tag)
            ns = anc.tag.split("}", 1)[0][1:] if isinstance(anc.tag, str) and "}" in anc.tag else ""
            if ns == MC_NS and name == "Fallback":
                skip = True  # VML duplicate of an mc:Choice text box
                break
            if name == "txbxContent":
                reason = reason or "text_box"
                container = container or Container.TEXT_BOX
            elif name == "sdt" and ns == W_NS:
                sdt_pr = anc.find(w("sdtPr"))
                if sdt_pr is not None and (sdt_pr.find(w("dataBinding")) is not None or sdt_pr.find(w("lock")) is not None):
                    reason = reason or "bound_or_locked_content_control"
                container = container or Container.CONTENT_CONTROL
            elif name == "tc" and container is None:
                container = Container.TABLE
        if not skip:
            yield p, reason, container


def _table_cell_ref(p: etree._Element) -> TableCellRef | None:
    tc = next((a for a in p.iterancestors(w("tc"))), None)
    if tc is None:
        return None
    tr = tc.getparent()
    tbl = tr.getparent() if tr is not None else None
    if tr is None or tbl is None:
        return None
    col = 0
    for cell in tr.findall(w("tc")):
        if cell is tc:
            break
        span = cell.find(f"{w('tcPr')}/{w('gridSpan')}")
        col += int(span.get(w("val"), "1")) if span is not None else 1
    row = tbl.findall(w("tr")).index(tr)
    depth = sum(1 for _ in p.iterancestors(w("tbl")))
    return TableCellRef(table_path=element_path(tbl), row=row, column=col, nesting_depth=depth)


def _style_names(pkg: DocxPackage) -> tuple[dict[str, str], dict[str, int]]:
    names: dict[str, str] = {}
    outline: dict[str, int] = {}
    styles_part = next((p for p in pkg.members if p.endswith("styles.xml") and p.startswith("word/")), None)
    if styles_part is None:
        return names, outline
    root = pkg.xml(styles_part)
    for st in root.findall(w("style")):
        sid = st.get(w("styleId"))
        name_el = st.find(w("name"))
        if sid:
            names[sid] = name_el.get(w("val")) if name_el is not None else sid
            lvl = st.find(f"{w('pPr')}/{w('outlineLvl')}")
            if lvl is not None:
                outline[sid] = int(lvl.get(w("val"), "9"))
    return names, outline


_HEADING_RE = re.compile(r"^(heading|title)\s*(\d)?$", re.IGNORECASE)


def _heading_level(style_name: str | None, p: etree._Element, style_outline: int | None) -> int | None:
    if style_name:
        m = _HEADING_RE.match(style_name.strip())
        if m:
            return int(m.group(2)) if m.group(2) else 0
    lvl = p.find(f"{w('pPr')}/{w('outlineLvl')}")
    if lvl is not None:
        val = int(lvl.get(w("val"), "9"))
        return val + 1 if val < 9 else None
    if style_outline is not None and style_outline < 9:
        return style_outline + 1
    return None


def _looks_like_section_label(text: str, p: etree._Element, is_list: bool) -> bool:
    stripped = text.strip()
    if is_list or not (2 <= len(stripped) <= 40) or not re.search(r"[A-Za-z]", stripped):
        return False
    if stripped.isupper():
        return True
    runs = [r for r in p.iter(w("r")) if (r.findtext(w("t")) or "").strip()]
    if not runs:
        return False
    for r in runs:
        b = r.find(f"{w('rPr')}/{w('b')}")
        if b is None or b.get(w("val"), "true") in {"0", "false"}:
            return False
    return len(stripped.split()) <= 5


def _run_format_count(p: etree._Element) -> tuple[int, int]:
    runs = list(p.iter(w("r")))
    formats = set()
    for r in runs:
        rpr = r.find(w("rPr"))
        formats.add(etree.tostring(rpr, method="c14n") if rpr is not None else b"")
    return len(runs), len(formats)


def _sections(pkg: DocxPackage) -> list[SectionInfo]:
    root = pkg.xml(pkg.main_part)
    sect_prs = list(root.iter(w("sectPr")))
    infos: list[SectionInfo] = []
    for idx, sp in enumerate(sect_prs):
        pg = sp.find(w("pgSz"))
        mar = sp.find(w("pgMar"))
        cols = sp.find(w("cols"))
        typ = sp.find(w("type"))
        infos.append(
            SectionInfo(
                index=idx,
                page_width=int(pg.get(w("w"))) if pg is not None and pg.get(w("w")) else None,
                page_height=int(pg.get(w("h"))) if pg is not None and pg.get(w("h")) else None,
                orientation=(pg.get(w("orient")) if pg is not None and pg.get(w("orient")) else "portrait"),
                margins={local_name(k): int(v) for k, v in (mar.attrib.items() if mar is not None else []) if v.lstrip("-").isdigit()},
                columns=int(cols.get(w("num"), "1")) if cols is not None else 1,
                section_type=typ.get(w("val")) if typ is not None else None,
                header_refs=len(sp.findall(w("headerReference"))),
                footer_refs=len(sp.findall(w("footerReference"))),
            )
        )
    return infos


def _features(pkg: DocxPackage, parts: list[tuple[str, Container]]) -> FeatureInventory:
    inv = FeatureInventory()
    for part, container in parts:
        root = pkg.xml(part)
        inv.paragraphs += sum(1 for _ in root.iter(w("p")))
        tables = list(root.iter(w("tbl")))
        inv.tables += len(tables)
        inv.nested_tables += sum(1 for t in tables if next(t.iterancestors(w("tbl")), None) is not None)
        inv.images += sum(1 for _ in root.iter(f"{{{A_NS}}}blip"))
        inv.hyperlinks += sum(1 for _ in root.iter(w("hyperlink")))
        inv.text_boxes += sum(1 for _ in root.iter(w("txbxContent")))
        inv.fields += sum(1 for _ in root.iter(w("fldSimple"))) + sum(
            1 for el in root.iter(w("fldChar")) if el.get(w("fldCharType")) == "begin"
        )
        inv.tracked_changes += sum(1 for tag in ("ins", "del", "moveTo", "moveFrom") for _ in root.iter(w(tag)))
        inv.comments += sum(1 for _ in root.iter(w("commentRangeStart")))
        inv.content_controls += sum(1 for _ in root.iter(w("sdt")))
        inv.math += sum(1 for _ in root.iter(f"{{{M_NS}}}oMath"))
        inv.alternate_content += sum(1 for _ in root.iter(f"{{{MC_NS}}}AlternateContent"))
        if container == Container.HEADER:
            inv.headers += 1
        elif container == Container.FOOTER:
            inv.footers += 1
    inv.footnotes = any(n.endswith("footnotes.xml") for n in pkg.members)
    inv.endnotes = any(n.endswith("endnotes.xml") for n in pkg.members)
    inv.embedded_objects = sum(1 for n in pkg.members if n.startswith("word/embeddings/"))
    inv.charts = sum(1 for n in pkg.members if n.startswith("word/charts/") and n.endswith(".xml") and "/_rels/" not in n)
    inv.smart_art = sum(1 for n in pkg.members if n.startswith("word/diagrams/data"))
    sections = _sections(pkg)
    inv.sections = len(sections)
    inv.multi_column_sections = sum(1 for s in sections if s.columns > 1)
    return inv


def _limitations(inv: FeatureInventory) -> list[str]:
    notes = []
    if inv.text_boxes:
        notes.append(f"{inv.text_boxes} text box(es): text inside text boxes is preserved but not editable.")
    if inv.tracked_changes:
        notes.append("Tracked changes present: affected text is read-only. Accept or reject changes in Word first for full editing.")
    if inv.fields:
        notes.append(f"{inv.fields} field(s) (e.g. page numbers, dates): field results are read-only.")
    if inv.content_controls:
        notes.append("Content controls present: inline, locked or data-bound controls are read-only.")
    if inv.math:
        notes.append("Equations are preserved but not editable.")
    if inv.footnotes or inv.endnotes:
        notes.append("Footnotes/endnotes are preserved but not editable.")
    if inv.embedded_objects or inv.charts or inv.smart_art:
        notes.append("Embedded objects, charts or SmartArt are preserved but not editable.")
    if inv.comments:
        notes.append("Comments are preserved; edits may not cross comment anchors.")
    return notes


def parse_docx(data: bytes) -> tuple[DocumentIndex, DocxPackage]:
    pkg = DocxPackage(data)
    parts = pkg.editable_parts()
    style_names, style_outline = _style_names(pkg)
    blocks: list[Block] = []
    current_section: str | None = None

    for part, part_container in parts:
        root = pkg.xml(part)
        for p, reason, container_override in iter_paragraphs(root):
            model = build_paragraph(p, reason)
            text = model.text
            path = element_path(p)
            ppr = p.find(w("pPr"))
            style_id = None
            if ppr is not None and ppr.find(w("pStyle")) is not None:
                style_id = ppr.find(w("pStyle")).get(w("val"))
            style_name = style_names.get(style_id, style_id) if style_id else None
            num_pr = ppr.find(w("numPr")) if ppr is not None else None
            is_list = num_pr is not None or bool(style_name and "list" in style_name.lower())
            list_level = None
            if num_pr is not None and num_pr.find(w("ilvl")) is not None:
                list_level = int(num_pr.find(w("ilvl")).get(w("val"), "0"))
            heading = _heading_level(style_name, p, style_outline.get(style_id or ""))
            container = container_override or part_container
            if part_container in (Container.HEADER, Container.FOOTER):
                container = part_container
            if part == pkg.main_part and text.strip() and (heading is not None or _looks_like_section_label(text, p, is_list)):
                current_section = text.strip()[:60]
            editable_text = any(s.kind == SegmentKind.TEXT and s.editable and s.text for s in model.segments)
            if reason is None and not editable_text and text.strip():
                reasons = sorted({s.reason for s in model.segments if s.kind == SegmentKind.TEXT and s.reason})
                reason = ",".join(reasons) or None
            run_count, formats = _run_format_count(p)
            th = text_hash(text)
            loc = "loc_" + hashlib.sha256(f"{pkg.version}|{part}|{path}|{th}".encode()).hexdigest()[:16]
            blocks.append(
                Block(
                    location_id=loc,
                    part=part,
                    path=path,
                    container=container,
                    section=current_section if part == pkg.main_part else container.value,
                    style_id=style_id,
                    style_name=style_name,
                    heading_level=heading,
                    is_list_item=is_list,
                    list_level=list_level,
                    text=text,
                    text_hash=th,
                    editable=editable_text and reason is None,
                    read_only_reason=reason if text.strip() else None,
                    run_count=run_count,
                    distinct_run_formats=formats,
                    has_hyperlink=any(s.hyperlink_key for s in model.segments),
                    has_field=any(s.reason == "field" or s.reason == "field_result" for s in model.segments),
                    table_cell=_table_cell_ref(p),
                )
            )

    features = _features(pkg, parts)
    index = DocumentIndex(
        document_version=pkg.version,
        main_part=pkg.main_part,
        blocks=blocks,
        sections=_sections(pkg),
        features=features,
        limitations=_limitations(features),
    )
    return index, pkg


__all__ = [
    "DocxPackage",
    "ParagraphModel",
    "Segment",
    "SegmentKind",
    "build_paragraph",
    "document_version",
    "element_path",
    "find_by_path",
    "iter_paragraphs",
    "parse_docx",
    "serialize_xml",
    "text_hash",
    "REL_TYPE_PREFIX",
]
