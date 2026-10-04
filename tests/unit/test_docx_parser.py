from __future__ import annotations

import pytest

from app.document.document_models import Container
from app.document.docx_parser import parse_docx
from app.document.location_index import LocationResolver, RangeError, TextRange, check_range_editable, locate_unique
from tests.conftest import fixture_bytes
from tests.support.docx_mutation import read_member, rebuild


def _block(index, needle):
    found = [b for b in index.blocks if needle in b.text]
    assert len(found) == 1, f"{needle!r} matched {len(found)} blocks"
    return found[0]


def test_location_ids_are_deterministic():
    data = fixture_bytes("simple_one_page")
    a, _ = parse_docx(data)
    b, _ = parse_docx(data)
    assert [x.location_id for x in a.blocks] == [x.location_id for x in b.blocks]
    assert len({x.location_id for x in a.blocks}) == len(a.blocks)


def test_location_ids_are_bound_to_document_version():
    data = fixture_bytes("simple_one_page")
    doc_xml = read_member(data, "word/document.xml").replace(b"Python, FastAPI", b"Python, Flask", 1)
    changed = rebuild(data, replace={"word/document.xml": doc_xml})
    a, _ = parse_docx(data)
    b, _ = parse_docx(changed)
    assert a.document_version != b.document_version
    # Even paragraphs whose text did not change get new ids in a new version.
    assert _block(a, "Built REST APIs").location_id != _block(b, "Built REST APIs").location_id


def test_headings_sections_and_lists():
    index, _ = parse_docx(fixture_bytes("simple_one_page"))
    assert _block(index, "Jordan Lee").heading_level == 0
    assert _block(index, "Summary").heading_level == 1
    bullet = _block(index, "Built REST APIs")
    assert bullet.section == "Experience"
    assert bullet.is_list_item
    assert _block(index, "B.Sc. Computer Science").section == "Education"


def test_text_spanning_runs_is_concatenated():
    index, _ = parse_docx(fixture_bytes("rich_runs"))
    blk = _block(index, "Led project management")
    assert blk.text.startswith("Led project management for a 6-person team")
    assert blk.run_count == 3
    assert blk.distinct_run_formats == 2


def test_tables_and_nested_tables():
    index, _ = parse_docx(fixture_bytes("tables"))
    cell = _block(index, "Maintained CI pipelines")
    assert cell.container == Container.TABLE
    assert cell.table_cell is not None and (cell.table_cell.row, cell.table_cell.column) == (1, 1)
    nested = _block(index, "Certified Kubernetes Administrator")
    assert nested.table_cell is not None and nested.table_cell.nesting_depth == 2
    assert index.features.tables == 2 and index.features.nested_tables == 1


def test_headers_footers_hyperlinks_fields():
    index, _ = parse_docx(fixture_bytes("header_footer_links"))
    header = _block(index, "portfolio.example.com")
    assert header.container == Container.HEADER and header.has_hyperlink
    footer = _block(index, "Page ")
    assert footer.container == Container.FOOTER
    assert footer.has_field
    assert footer.text == "Page 1"  # field code (instrText) is not visible text
    assert index.features.headers == 1 and index.features.footers == 1
    assert index.features.hyperlinks == 2


def test_sections_columns_orientation():
    index, _ = parse_docx(fixture_bytes("columns_sections"))
    assert len(index.sections) == 3
    assert [s.columns for s in index.sections] == [1, 2, 1]
    assert index.sections[2].orientation == "landscape"
    assert index.features.multi_column_sections == 1


def test_images_counted():
    index, _ = parse_docx(fixture_bytes("images"))
    assert index.features.images == 2


def test_complex_elements_read_only_and_reported():
    index, _ = parse_docx(fixture_bytes("complex_elements"))
    text_box = _block(index, "Text box: open to relocation")
    assert not text_box.editable and text_box.read_only_reason == "text_box"
    assert text_box.container == Container.TEXT_BOX
    tracked = _block(index, "Operations lead.")
    assert "Inserted by tracked change." in tracked.text
    assert "Deleted text" not in index.full_text()  # w:delText is not visible
    assert "DATE" not in index.full_text()  # field instructions are not visible
    f = index.features
    assert f.text_boxes == 1 and f.tracked_changes == 2 and f.content_controls == 1 and f.fields == 1
    assert any("Tracked changes" in note for note in index.limitations)
    assert any("text box" in note for note in index.limitations)


def test_vml_fallback_duplicates_are_not_indexed():
    index, _ = parse_docx(fixture_bytes("complex_elements"))
    assert sum("Text box: open to relocation" in b.text for b in index.blocks) == 1


# ---------------------------------------------------------------- location rules


def _model(fixture_id, needle):
    data = fixture_bytes(fixture_id)
    index, pkg = parse_docx(data)
    blk = _block(index, needle)
    return blk, LocationResolver(pkg).paragraph(blk.part, blk.path)


def test_locate_unique_rules():
    assert locate_unique("abc def abc", "def") == TextRange(4, 7)
    with pytest.raises(RangeError) as exc:
        locate_unique("abc def abc", "abc")
    assert exc.value.code == "ambiguous_target"
    with pytest.raises(RangeError) as exc:
        locate_unique("abc", "xyz")
    assert exc.value.code == "target_not_found"
    with pytest.raises(RangeError) as exc:
        locate_unique("abc", "")
    assert exc.value.code == "empty_target"


@pytest.mark.parametrize(
    ("fixture_id", "paragraph", "target", "code"),
    [
        ("complex_elements", "Ran the warehouse", "warehouse night", "crosses_object"),  # bookmark start inside range
        ("complex_elements", "Availability:", "Immediately", "read_only_text"),  # inline content control
        ("complex_elements", "Operations lead.", "Inserted by", "read_only_text"),  # tracked insertion
        ("header_footer_links", "Frontend engineer", "work at github", "crosses_hyperlink"),
        ("images", "Portfolio sample", "sample:  shown", "crosses_object"),  # inline image
        ("header_footer_links", "Page ", "1", "read_only_text"),  # field result
        ("header_footer_links", "Page ", "Page 1", "crosses_object"),  # field boundary
    ],
)
def test_range_editability_rules(fixture_id, paragraph, target, code):
    blk, model = _model(fixture_id, paragraph)
    with pytest.raises(RangeError) as exc:
        check_range_editable(model, locate_unique(blk.text, target))
    assert exc.value.code == code


def test_range_fully_inside_hyperlink_is_editable():
    blk, model = _model("header_footer_links", "Frontend engineer")
    check_range_editable(model, locate_unique(blk.text, "github.com"))


def test_range_spanning_runs_is_editable():
    blk, model = _model("rich_runs", "Designed event-driven")
    check_range_editable(model, locate_unique(blk.text, "pipelines using Kafka for"))
