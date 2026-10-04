"""Bounded, structured resume context for the model.

Only paragraph text, location ids, section labels and an editability flag are
sent. No XML, file names, document properties or contact details beyond what
appears in the visible text are included. Text is wrapped in data delimiters
and the model is told to treat it as untrusted.
"""

from __future__ import annotations

import json

from app.document.document_models import DocumentIndex


def build_resume_context(index: DocumentIndex, char_budget: int) -> tuple[str, list[str], bool]:
    """Return (JSON lines context, included location ids, truncated flag)."""
    lines: list[str] = []
    included: list[str] = []
    used = 0
    truncated = False
    for blk in index.blocks:
        if not blk.text.strip():
            continue
        entry = {
            "id": blk.location_id,
            "section": blk.section,
            "where": blk.container.value,
            "editable": blk.editable,
            "text": blk.text,
        }
        line = json.dumps(entry, ensure_ascii=False)
        if used + len(line) > char_budget:
            truncated = True
            break
        lines.append(line)
        included.append(blk.location_id)
        used += len(line) + 1
    return "\n".join(lines), included, truncated
