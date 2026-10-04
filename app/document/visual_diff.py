"""Gate D: region-aware visual comparison of rendered documents.

Locating the expected change regions
------------------------------------
Two extra *marker* copies are rendered (original and output) in which every
run of an edited paragraph is coloured magenta. Colour does not affect
layout, so the marked renders have the same geometry as the plain renders
(this is verified on every run). Magenta glyph boxes give the exact regions
where change is expected; everything else is context that should not change.

Checks
------
* page count delta and blank pages;
* context words (outside edited paragraphs) aligned by sequence diff:
  missing/extra words, font, size or colour changes, shifts *before* the first edit,
  movement to another page, and line-wrap changes (words of one original line
  no longer sharing a line with the same spacing);
* pure vertical reflow below an edit on the same page is allowed (info);
* images (count and size) and vector graphics (table borders, rules);
* text outside the page box;
* pixel differences outside expected regions, restricted to areas not
  affected by legitimate reflow, reported as connected regions;
* per-page SSIM is recorded as a metric only, never as the gate.
"""

from __future__ import annotations

import difflib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from lxml import etree

from app.document.document_models import DocumentIndex
from app.document.docx_parser import DocxPackage, serialize_xml
from app.document.fidelity_models import Finding, GateResult, PageCount, Severity
from app.document.location_index import LocationResolver
from app.document.ooxml import w

MARKER_RGB = "FF00FF"


@dataclass(frozen=True)
class VisualThresholds:
    """Defaults calibrated on the fixture corpus; see docs/FIDELITY_EVALUATION.md."""

    position_tolerance_pt: float = 0.75
    size_tolerance_pt: float = 0.1
    pixel_threshold: int = 32
    min_region_area_px: int = 16
    expected_region_padding_px: int = 4
    graphics_tolerance_pt: float = 0.5


@dataclass
class Word:
    text: str
    page: int
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    font: str
    marked: bool
    color: tuple[float, ...] | None = None


@dataclass
class PageLayout:
    width: float
    height: float
    words: list[Word] = field(default_factory=list)
    images: list[tuple[float, float, float, float]] = field(default_factory=list)
    graphics: list[tuple[float, float, float, float]] = field(default_factory=list)


@dataclass
class VisualAnalysis:
    gate: GateResult
    page_count: PageCount
    layout_warnings: list[str]
    unexpected_regions: int
    artifacts: dict[str, str]


# --------------------------------------------------------------------------- marker copies


def make_marker_copy(data: bytes, index: DocumentIndex, location_ids: set[str]) -> bytes:
    pkg = DocxPackage(data)
    resolver = LocationResolver(pkg)
    touched: set[str] = set()
    for blk in index.blocks:
        if blk.location_id not in location_ids:
            continue
        model = resolver.paragraph(blk.part, blk.path)
        if model is None:
            continue
        for run in model.element.iter(w("r")):
            rpr = run.find(w("rPr"))
            if rpr is None:
                rpr = etree.SubElement(run, w("rPr"))
                run.remove(rpr)
                run.insert(0, rpr)
            color = rpr.find(w("color"))
            if color is None:
                color = etree.SubElement(rpr, w("color"))
            for attr in list(color.attrib):
                del color.attrib[attr]
            color.set(w("val"), MARKER_RGB)
        touched.add(blk.part)
    return pkg.write({part: serialize_xml(pkg.xml(part)) for part in touched})


# --------------------------------------------------------------------------- extraction


def _is_marker(color: object) -> bool:
    if not isinstance(color, (tuple, list)) or len(color) != 3:
        return False
    r, g, b = (float(c) for c in color)
    return r > 0.97 and g < 0.03 and b > 0.97


def _color_key(color: object) -> tuple[float, ...] | None:
    if isinstance(color, (int, float)):
        return (round(float(color), 2),)
    if isinstance(color, (tuple, list)):
        return tuple(round(float(c), 2) for c in color)
    return None


def _font(name: str) -> str:
    return name.split("+", 1)[-1] if name else ""


def extract_layout(pdf_path: Path) -> list[PageLayout]:
    import pdfplumber

    pages: list[PageLayout] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for idx, page in enumerate(pdf.pages):
            layout = PageLayout(width=float(page.width), height=float(page.height))
            for wd in page.extract_words(
                extra_attrs=["non_stroking_color", "size", "fontname"], use_text_flow=True, keep_blank_chars=False
            ):
                layout.words.append(
                    Word(
                        text=wd["text"],
                        page=idx,
                        x0=float(wd["x0"]),
                        x1=float(wd["x1"]),
                        top=float(wd["top"]),
                        bottom=float(wd["bottom"]),
                        size=float(wd["size"]),
                        font=_font(wd["fontname"]),
                        marked=_is_marker(wd["non_stroking_color"]),
                        color=_color_key(wd["non_stroking_color"]),
                    )
                )
            for im in page.images:
                layout.images.append((float(im["x0"]), float(im["top"]), float(im["x1"]), float(im["bottom"])))
            for obj in list(page.rects) + list(page.lines) + list(page.curves):
                layout.graphics.append((float(obj["x0"]), float(obj["top"]), float(obj["x1"]), float(obj["bottom"])))
            pages.append(layout)
    return pages


def _flatten(pages: list[PageLayout]) -> list[Word]:
    return [wd for p in pages for wd in p.words]


def _same_geometry(a: list[PageLayout], b: list[PageLayout], tol: float) -> bool:
    wa, wb = _flatten(a), _flatten(b)
    if len(wa) != len(wb):
        return False
    return all(
        x.text == y.text and x.page == y.page and abs(x.x0 - y.x0) <= tol and abs(x.top - y.top) <= tol for x, y in zip(wa, wb, strict=True)
    )


# --------------------------------------------------------------------------- analysis


def _blank(page: PageLayout) -> bool:
    return not page.words and not page.images and not page.graphics


def _round_box(box: tuple[float, float, float, float], step: float) -> tuple[float, ...]:
    return tuple(round(v / step) * step for v in box)


def _size(box: tuple[float, float, float, float], step: float) -> tuple[float, float]:
    return (round((box[2] - box[0]) / step) * step, round((box[3] - box[1]) / step) * step)


def analyze_visual(
    original_pdf: Path,
    output_pdf: Path,
    original_marked_pdf: Path,
    output_marked_pdf: Path,
    original_pages: list[Path],
    output_pages: list[Path],
    dpi: int,
    thresholds: VisualThresholds = VisualThresholds(),
    artifact_dir: Path | None = None,
    compute_ssim: bool = True,
) -> VisualAnalysis:
    tol = thresholds.position_tolerance_pt
    findings: list[Finding] = []
    warnings: list[str] = []
    artifacts: dict[str, str] = {}

    orig = extract_layout(original_pdf)
    out = extract_layout(output_pdf)
    orig_m = extract_layout(original_marked_pdf)
    out_m = extract_layout(output_marked_pdf)
    page_count = PageCount(original=len(orig), output=len(out))
    metrics: dict[str, object] = {"pages_original": len(orig), "pages_output": len(out)}

    # Marker self-check: colour must not change layout.
    if not (_same_geometry(orig, orig_m, 0.01) and _same_geometry(out, out_m, 0.01)):
        findings.append(
            Finding(
                code="marker_render_inconsistent",
                severity=Severity.WARNING,
                message="Edited-region markers changed the rendered layout; region attribution is unreliable.",
            )
        )

    if len(out) != len(orig):
        msg = f"Page count changed from {len(orig)} to {len(out)}."
        warnings.append(msg)
        findings.append(
            Finding(code="page_count_changed", severity=Severity.WARNING, message=msg, details={"original": len(orig), "output": len(out)})
        )

    blank_before = sum(1 for p in orig if _blank(p))
    blank_after = sum(1 for p in out if _blank(p))
    if blank_after > blank_before:
        findings.append(
            Finding(code="blank_page_introduced", severity=Severity.ERROR, message="The edited document contains an unexpected blank page.")
        )

    # ---- words: alignment of context (non-edited) words
    a_all, b_all = _flatten(orig_m), _flatten(out_m)
    a_ctx = [x for x in a_all if not x.marked]
    b_ctx = [x for x in b_all if not x.marked]
    first_marked = next((i for i, x in enumerate(a_all) if x.marked), None)
    pre_edit_ids = {id(x) for x in (a_all[:first_marked] if first_marked is not None else a_all)}

    matcher = difflib.SequenceMatcher(a=[x.text for x in a_ctx], b=[x.text for x in b_ctx], autojunk=False)
    pairs: list[tuple[Word, Word]] = []
    missing = extra = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pairs.extend(zip(a_ctx[i1:i2], b_ctx[j1:j2], strict=True))
        else:
            missing += i2 - i1
            extra += j2 - j1
    if missing or extra:
        findings.append(
            Finding(
                code="context_text_render_mismatch",
                severity=Severity.WARNING,
                message=f"Rendered text outside edited paragraphs differs ({missing} missing, {extra} extra words).",
                details={"missing": missing, "extra": extra},
            )
        )

    moved_pre_edit = moved_page = font_changes = color_changes = 0
    reflow_top: dict[int, float] = {}
    page_shifts: dict[int, list[float]] = {}
    for a, b in pairs:
        if a.page == b.page and abs(a.x0 - b.x0) <= tol and abs(a.top - b.top) > tol:
            page_shifts.setdefault(a.page, []).append(b.top - a.top)
        if _font(a.font) != _font(b.font) or abs(a.size - b.size) > thresholds.size_tolerance_pt:
            font_changes += 1
        if a.color != b.color:
            color_changes += 1
        stable = a.page == b.page and abs(a.x0 - b.x0) <= tol and abs(a.top - b.top) <= tol
        if stable:
            continue
        if id(a) in pre_edit_ids:
            moved_pre_edit += 1
        if a.page != b.page:
            moved_page += 1
        for page_idx, top in ((a.page, a.top), (b.page, b.top)):
            reflow_top[page_idx] = min(reflow_top.get(page_idx, top), top)
    if font_changes:
        findings.append(
            Finding(
                code="font_changed",
                severity=Severity.WARNING,
                message=f"Font or size changed for {font_changes} word(s) outside edited text.",
            )
        )
    if color_changes:
        findings.append(
            Finding(
                code="text_color_changed",
                severity=Severity.WARNING,
                message=f"Text colour changed for {color_changes} word(s) outside edited text.",
            )
        )
    if moved_pre_edit:
        findings.append(
            Finding(code="shift_before_edit", severity=Severity.WARNING, message=f"{moved_pre_edit} word(s) before the first edit moved.")
        )
    if moved_page:
        msg = f"{moved_page} word(s) moved to a different page (page breaks shifted)."
        warnings.append(msg)
        findings.append(Finding(code="page_flow_changed", severity=Severity.WARNING, message=msg))
    reflowed = sum(1 for a, b in pairs if not (a.page == b.page and abs(a.x0 - b.x0) <= tol and abs(a.top - b.top) <= tol))
    if reflowed and not moved_page and not moved_pre_edit:
        findings.append(
            Finding(
                code="vertical_reflow",
                severity=Severity.INFO,
                message=f"{reflowed} word(s) after an edit shifted vertically on the same page.",
            )
        )

    # Line integrity: words that shared a line must still share a line with the same spacing.
    pair_of = {id(a): b for a, b in pairs}
    lines: dict[tuple[int, float], list[Word]] = {}
    for a in a_ctx:
        lines.setdefault((a.page, round(a.top, 1)), []).append(a)
    broken_lines = 0
    for words in lines.values():
        matched = [(a, pair_of[id(a)]) for a in words if id(a) in pair_of]
        if len(matched) < 2:
            continue
        b0 = matched[0][1]
        a0 = matched[0][0]
        for a, b in matched[1:]:
            if b.page != b0.page or abs(b.top - b0.top) > tol or abs((b.x0 - b0.x0) - (a.x0 - a0.x0)) > tol:
                broken_lines += 1
                break
    if broken_lines:
        msg = f"Line wrapping or horizontal layout changed on {broken_lines} line(s) outside edited text."
        warnings.append(msg)
        findings.append(Finding(code="line_wrap_changed", severity=Severity.WARNING, message=msg))

    # Edited text line counts (informational).
    def _marked_lines(pages: list[PageLayout]) -> int:
        return len({(x.page, round(x.top, 1)) for x in _flatten(pages) if x.marked})

    lines_before, lines_after = _marked_lines(orig_m), _marked_lines(out_m)
    metrics.update(
        {
            "edited_lines_original": lines_before,
            "edited_lines_output": lines_after,
            "context_words": len(a_ctx),
            "context_words_moved": reflowed,
        }
    )
    if lines_before != lines_after:
        findings.append(
            Finding(
                code="edited_text_line_count_changed",
                severity=Severity.INFO,
                message=f"Edited text now spans {lines_after} line(s) (was {lines_before}).",
            )
        )

    # Text outside page box.
    def _overflow(pages: list[PageLayout]) -> int:
        return sum(1 for p in pages for x in p.words if x.x0 < -0.5 or x.x1 > p.width + 0.5 or x.bottom > p.height + 0.5)

    if _overflow(out) > _overflow(orig):
        findings.append(Finding(code="text_outside_page", severity=Severity.WARNING, message="Text extends beyond the page boundary."))

    # ---- images and vector graphics
    a_imgs = [box for p in orig for box in p.images]
    b_imgs = [box for p in out for box in p.images]
    if len(b_imgs) < len(a_imgs):
        findings.append(
            Finding(
                code="image_missing",
                severity=Severity.ERROR,
                message=f"{len(a_imgs) - len(b_imgs)} image(s) missing from the rendered output.",
            )
        )
    elif Counter(_size(b, 1.0) for b in a_imgs) != Counter(_size(b, 1.0) for b in b_imgs):
        findings.append(Finding(code="image_size_changed", severity=Severity.WARNING, message="Rendered image sizes changed."))

    g = thresholds.graphics_tolerance_pt
    for idx in range(min(len(orig), len(out))):
        if idx in reflow_top:
            if Counter(_size(b, g * 2) for b in orig[idx].graphics) != Counter(_size(b, g * 2) for b in out[idx].graphics):
                findings.append(
                    Finding(
                        code="graphics_changed_on_reflowed_page",
                        severity=Severity.INFO,
                        message=f"Vector graphics changed on reflowed page {idx + 1} (expected if an edited table cell grew).",
                    )
                )
        elif sorted(_round_box(b, g) for b in orig[idx].graphics) != sorted(_round_box(b, g) for b in out[idx].graphics):
            findings.append(
                Finding(
                    code="graphics_changed", severity=Severity.WARNING, message=f"Table borders or drawn shapes changed on page {idx + 1}."
                )
            )

    # ---- pixel comparison outside expected regions
    unexpected_regions, ssim_scores = _pixel_regions(
        orig_m,
        out_m,
        original_pages,
        output_pages,
        dpi,
        reflow_top,
        thresholds,
        artifact_dir,
        artifacts,
        compute_ssim,
        {k: float(np.median(v)) for k, v in page_shifts.items()},
    )
    metrics["ssim_per_page"] = ssim_scores
    metrics["unexpected_pixel_regions"] = unexpected_regions
    if unexpected_regions:
        findings.append(
            Finding(
                code="unexpected_visual_change",
                severity=Severity.WARNING,
                message=f"{unexpected_regions} changed region(s) found outside the edited text.",
            )
        )

    gate = GateResult.from_findings("visual", findings, metrics)
    return VisualAnalysis(
        gate=gate, page_count=page_count, layout_warnings=warnings, unexpected_regions=unexpected_regions, artifacts=artifacts
    )


def _tolerant_shift_diff(a_rgb: np.ndarray, target: np.ndarray, start: int, dy: int, rows: int, threshold: int) -> np.ndarray:
    """Differences between ``target`` and ``a_rgb`` shifted down by ``dy`` (+-1 px), tolerant to 1 px.

    Text reflowed by a non-integer amount rasterises with different anti-aliasing, so a
    pixel counts as changed only if no pixel within a 3x3 neighbourhood of any candidate
    offset matches it. Solid changes (fills, highlights, recolouring) remain detected.
    """
    best = np.full(target.shape[:2], 255, dtype=np.int16)
    width = target.shape[1]
    for d in (dy - 1, dy, dy + 1):
        for oy in (-1, 0, 1):
            src_top = start - d + oy
            if src_top < 0 or src_top + rows > a_rgb.shape[0]:
                continue
            band = a_rgb[src_top : src_top + rows]
            for ox in (-1, 0, 1):
                shifted = np.roll(band, ox, axis=1)
                delta = np.abs(shifted.astype(np.int16) - target.astype(np.int16)).max(axis=2)
                if ox != 0:
                    delta[:, [0, width - 1]] = 255
                best = np.minimum(best, delta)
    return (best > threshold).astype(np.uint8)


def _last_marked_bottom(pages: list[PageLayout], idx: int) -> float:
    if idx >= len(pages):
        return 0.0
    bottoms = [w.bottom for w in pages[idx].words if w.marked]
    return max(bottoms) if bottoms else 0.0


def _pixel_regions(
    orig_m: list[PageLayout],
    out_m: list[PageLayout],
    original_pages: list[Path],
    output_pages: list[Path],
    dpi: int,
    reflow_top: dict[int, float],
    t: VisualThresholds,
    artifact_dir: Path | None,
    artifacts: dict[str, str],
    compute_ssim: bool = True,
    page_shift_pt: dict[int, float] | None = None,
) -> tuple[int, list[float | None]]:
    import cv2
    from skimage.metrics import structural_similarity

    scale = dpi / 72.0
    total = 0
    ssim_scores: list[float | None] = []
    kernel = np.ones((2, 2), np.uint8)
    for idx in range(min(len(original_pages), len(output_pages))):
        a_rgb = cv2.imread(str(original_pages[idx]), cv2.IMREAD_COLOR)
        b_rgb = cv2.imread(str(output_pages[idx]), cv2.IMREAD_COLOR)
        if a_rgb is None or b_rgb is None or a_rgb.shape != b_rgb.shape:
            ssim_scores.append(None)
            total += 1
            continue
        a = cv2.cvtColor(a_rgb, cv2.COLOR_BGR2GRAY)
        b = cv2.cvtColor(b_rgb, cv2.COLOR_BGR2GRAY)
        ssim_scores.append(round(float(structural_similarity(a, b, data_range=255)), 5) if compute_ssim else None)
        # Per-channel maximum: grayscale would hide light colours (yellow on white differs by ~29 levels).
        diff = (cv2.absdiff(a_rgb, b_rgb).max(axis=2) > t.pixel_threshold).astype(np.uint8)
        expected = np.zeros_like(diff)
        pad = t.expected_region_padding_px
        for pages in (orig_m, out_m):
            if idx < len(pages):
                for wd in pages[idx].words:
                    if wd.marked:
                        x0, y0 = int(wd.x0 * scale) - pad, int(wd.top * scale) - pad
                        x1, y1 = int(wd.x1 * scale) + pad, int(wd.bottom * scale) + pad
                        expected[max(0, y0) : y1, max(0, x0) : x1] = 1
        diff[expected == 1] = 0
        if idx in reflow_top:
            cut = max(0, int(reflow_top[idx] * scale) - pad)
            diff[cut:, :] = 0
            # Shift-compensated comparison below the edit: if content on this page moved down by a
            # consistent offset, the shifted original should match the output pixel-for-pixel.
            shift = (page_shift_pt or {}).get(idx)
            if shift is not None and shift > 0:
                dy = int(round(shift * scale))
                start = max(cut, int(_last_marked_bottom(out_m, idx) * scale) + pad)
                height = a_rgb.shape[0] - start
                if 0 < dy < height - 2 and start - dy - 2 >= 0:
                    rows = height - dy - 1
                    target = b_rgb[start : start + rows]
                    shifted = _tolerant_shift_diff(a_rgb, target, start, dy, rows, t.pixel_threshold)
                    diff[start : start + rows, :] = np.maximum(diff[start : start + rows, :], shifted)
        diff = cv2.morphologyEx(diff, cv2.MORPH_OPEN, kernel).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(diff, connectivity=8)
        regions = [stats[i] for i in range(1, count) if stats[i][cv2.CC_STAT_AREA] >= t.min_region_area_px]
        total += len(regions)
        if regions and artifact_dir is not None:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            left = a_rgb.copy()
            right = b_rgb.copy()
            for s in regions:
                x, y, wdt, hgt = (int(v) for v in s[:4])
                for img in (left, right):
                    cv2.rectangle(img, (x - 2, y - 2), (x + wdt + 2, y + hgt + 2), (0, 0, 255), 2)
            path = artifact_dir / f"visual_diff_page_{idx + 1}.png"
            cv2.imwrite(str(path), np.hstack([left, right]))
            artifacts[f"visual_diff_page_{idx + 1}"] = path.name
    return total, ssim_scores
