from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from docx import Document

from app.document.docx_editor import EditOperation, apply_edits, plan_replacement
from app.document.docx_parser import parse_docx
from app.document.location_index import locate_unique
from tests.conftest import fixture_bytes


def _op(index, needle, original, new, edit_id="e1") -> EditOperation:
    blk = next(b for b in index.blocks if needle in b.text)
    rng = locate_unique(blk.text, original)
    return EditOperation(edit_id=edit_id, location_id=blk.location_id, start=rng.start, end=rng.end, expected_text=original, new_text=new)


def _members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _runs(data: bytes, needle: str):
    doc = Document(io.BytesIO(data))
    p = next(p for p in doc.paragraphs if needle in p.text)
    return [(r.text, r.bold, r.italic, r.underline, str(r.font.color.rgb) if r.font.color.rgb else None) for r in p.runs]


def test_single_edit_applied_and_other_parts_untouched():
    data = fixture_bytes("simple_one_page")
    index, _ = parse_docx(data)
    result = apply_edits(data, index, [_op(index, "Worked on the migration", "Worked on the migration of", "Migrated")])
    assert result.ok, result.failures
    out_index, _ = parse_docx(result.output)
    assert any(b.text == "Migrated the billing system to PostgreSQL." for b in out_index.blocks)
    before, after = _members(data), _members(result.output)
    assert set(before) == set(after)
    assert [n for n in before if before[n] != after[n]] == ["word/document.xml"]


def test_formatting_of_unchanged_words_preserved_across_runs():
    data = fixture_bytes("rich_runs")
    index, _ = parse_docx(data)
    op = _op(
        index,
        "Designed event-driven",
        "data pipelines using Kafka for the analytics team",
        "data pipelines using Kafka for analytics reporting",
    )
    result = apply_edits(data, index, [op])
    assert result.ok
    runs = _runs(result.output, "Designed")
    assert runs[1] == ("event-driven", True, None, None, None)
    assert runs[2] == (" data pipelines ", None, True, None, None)
    assert runs[3] == ("using Kafka", None, None, True, "C00000")  # underline + red kept
    assert runs[4][0] == " for analytics reporting."


def test_run_count_and_properties_never_change():
    data = fixture_bytes("rich_runs")
    index, _ = parse_docx(data)
    op = _op(index, "Led project management", "Led project management", "Managed projects")
    result = apply_edits(data, index, [op])
    assert result.ok
    before = [r[1:] for r in _runs(data, "Led project")]
    after = [r[1:] for r in _runs(result.output, "Managed projects")]
    assert before == after


def test_multiple_non_overlapping_edits_in_one_paragraph():
    data = fixture_bytes("simple_one_page")
    index, _ = parse_docx(data)
    ops = [
        _op(index, "Responsible for code reviews", "Responsible for", "Led", "a"),
        _op(index, "Responsible for code reviews", "mentoring two junior engineers", "mentored two junior engineers", "b"),
    ]
    result = apply_edits(data, index, ops)
    assert result.ok, result.failures
    out_index, _ = parse_docx(result.output)
    assert any(b.text == "Led code reviews and mentored two junior engineers." for b in out_index.blocks)


def test_overlapping_edits_rejected_atomically():
    data = fixture_bytes("simple_one_page")
    index, _ = parse_docx(data)
    ops = [
        _op(index, "Responsible for code reviews", "Responsible for code", "Owned code", "a"),
        _op(index, "Responsible for code reviews", "code reviews", "reviews", "b"),
    ]
    result = apply_edits(data, index, ops)
    assert not result.ok and result.output is None
    assert any(f.code == "overlapping_edits" for f in result.failures)


def test_stale_source_rejected():
    data = fixture_bytes("simple_one_page")
    other = fixture_bytes("rich_runs")
    index, _ = parse_docx(data)
    result = apply_edits(other, index, [_op(index, "Worked on", "Worked on", "Drove")])
    assert not result.ok and result.failures[0].code == "stale_source"


def test_reapplying_to_output_is_impossible():
    """Retrying never double-applies: edits are bound to the original version."""
    data = fixture_bytes("simple_one_page")
    index, _ = parse_docx(data)
    op = _op(index, "Worked on the migration", "Worked on the migration of", "Migrated")
    first = apply_edits(data, index, [op])
    second = apply_edits(first.output, index, [op])
    assert not second.ok and second.failures[0].code == "stale_source"


def test_text_mismatch_rejected():
    data = fixture_bytes("simple_one_page")
    index, _ = parse_docx(data)
    op = _op(index, "Worked on the migration", "Worked on", "Drove")
    op = op.model_copy(update={"expected_text": "Walked on"})
    result = apply_edits(data, index, [op])
    assert not result.ok and result.failures[0].code == "text_mismatch"


def test_read_only_location_rejected():
    data = fixture_bytes("complex_elements")
    index, _ = parse_docx(data)
    blk = next(b for b in index.blocks if "Text box" in b.text)
    op = EditOperation(edit_id="x", location_id=blk.location_id, start=0, end=4, expected_text="Text", new_text="Note")
    result = apply_edits(data, index, [op])
    assert not result.ok and result.failures[0].code == "read_only_location"


def test_unknown_location_rejected():
    data = fixture_bytes("simple_one_page")
    index, _ = parse_docx(data)
    op = EditOperation(edit_id="x", location_id="loc_doesnotexist", start=0, end=1, expected_text="x", new_text="y")
    assert apply_edits(data, index, [op]).failures[0].code == "unknown_location"


@pytest.mark.parametrize("bad", ["two\nlines", "tab\there", "nul\x00char", "para sep"])
def test_structural_characters_rejected(bad):
    data = fixture_bytes("simple_one_page")
    index, _ = parse_docx(data)
    result = apply_edits(data, index, [_op(index, "Worked on", "Worked on", bad)])
    assert not result.ok and result.failures[0].code == "invalid_characters"


def test_duplicate_edit_ids_rejected():
    data = fixture_bytes("simple_one_page")
    index, _ = parse_docx(data)
    op = _op(index, "Worked on", "Worked on", "Drove")
    result = apply_edits(data, index, [op, op])
    assert not result.ok and result.failures[0].code == "duplicate_edit"


def test_edit_is_deterministic_and_original_untouched():
    data = fixture_bytes("tables")
    digest = hashlib.sha256(data).hexdigest()
    index, _ = parse_docx(data)
    op = _op(index, "Maintained CI pipelines", "Maintained CI pipelines", "Maintained and improved CI pipelines")
    a = apply_edits(data, index, [op]).output
    b = apply_edits(data, index, [op]).output
    assert a == b
    assert hashlib.sha256(data).hexdigest() == digest


def test_header_edit_changes_only_header_part():
    data = fixture_bytes("header_footer_links")
    index, _ = parse_docx(data)
    op = _op(index, "portfolio.example.com", "Jordan Lee", "Jordan A. Lee")
    result = apply_edits(data, index, [op])
    assert result.ok
    before, after = _members(data), _members(result.output)
    changed = [n for n in before if before[n] != after[n]]
    assert len(changed) == 1 and "header" in changed[0]


def test_whitespace_preserved_with_xml_space():
    data = fixture_bytes("simple_one_page")
    index, _ = parse_docx(data)
    result = apply_edits(data, index, [_op(index, "Worked on", "Worked on the", "Worked on  the")])
    assert result.ok
    out_index, _ = parse_docx(result.output)
    assert any("Worked on  the migration" in b.text for b in out_index.blocks)


def test_plan_replacement_keeps_owners_of_unchanged_tokens():
    old = "using Kafka for the team"
    owners = [0] * 6 + [1] * 5 + [2] * (len(old) - 11)
    planned = plan_replacement(old, owners, "using Kafka for analytics")
    text = "".join(c for c, _ in planned)
    assert text == "using Kafka for analytics"
    assert [o for c, o in planned][:11] == owners[:11]
    assert all(o == 2 for c, o in planned[11:])
