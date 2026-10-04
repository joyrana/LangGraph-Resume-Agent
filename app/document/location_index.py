"""Resolving locations and text ranges against a parsed document.

Shared by the evidence validator (at proposal time) and the editor (at
application time) so both enforce exactly the same editability rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from app.document.docx_parser import (
    DocxPackage,
    ParagraphModel,
    SegmentKind,
    build_paragraph,
    element_path,
    iter_paragraphs,
)


@dataclass(frozen=True)
class TextRange:
    start: int
    end: int


class RangeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def locate_unique(text: str, needle: str) -> TextRange:
    """Find ``needle`` exactly once in ``text``; otherwise raise."""
    if not needle:
        raise RangeError("empty_target", "The target text is empty.")
    first = text.find(needle)
    if first < 0:
        raise RangeError("target_not_found", "The quoted original text does not occur at this location.")
    if text.find(needle, first + 1) >= 0:
        raise RangeError("ambiguous_target", "The quoted original text occurs more than once at this location.")
    return TextRange(first, first + len(needle))


def check_range_editable(model: ParagraphModel, rng: TextRange) -> None:
    """Raise ``RangeError`` unless every character in the range is plain, editable ``w:t`` text."""
    text_len = len(model.text)
    if not (0 <= rng.start < rng.end <= text_len):
        raise RangeError("range_out_of_bounds", "The edit range is outside the paragraph text.")
    hyperlink_keys: set[str | None] = set()
    for seg in model.segments:
        if seg.start == seg.end:
            if rng.start < seg.start < rng.end:
                label = (seg.reason or "marker").replace("_", " ")
                raise RangeError("crosses_object", f"The edit range crosses a non-text element ({label}).")
            continue
        if seg.end <= rng.start or seg.start >= rng.end:
            continue
        if seg.kind != SegmentKind.TEXT:
            raise RangeError("crosses_layout_character", "The edit range includes a tab or line break, which edits must not change.")
        if not seg.editable:
            raise RangeError(
                "read_only_text", f"The edit range includes read-only text ({(seg.reason or 'unsupported').replace('_', ' ')})."
            )
        hyperlink_keys.add(seg.hyperlink_key)
    if len(hyperlink_keys) > 1:
        raise RangeError("crosses_hyperlink", "The edit range crosses a hyperlink boundary.")


class LocationResolver:
    """Maps (part, path) to live paragraph elements in a package's parsed XML."""

    def __init__(self, pkg: DocxPackage) -> None:
        self.pkg = pkg
        self._cache: dict[str, dict[str, tuple[etree._Element, str | None]]] = {}

    def _paragraphs(self, part: str) -> dict[str, tuple[etree._Element, str | None]]:
        if part not in self._cache:
            root = self.pkg.xml(part)
            self._cache[part] = {element_path(p): (p, reason) for p, reason, _ in iter_paragraphs(root)}
        return self._cache[part]

    def paragraph(self, part: str, path: str) -> ParagraphModel | None:
        if part not in self.pkg.members:
            return None
        found = self._paragraphs(part).get(path)
        if found is None:
            return None
        p, reason = found
        return build_paragraph(p, reason)
