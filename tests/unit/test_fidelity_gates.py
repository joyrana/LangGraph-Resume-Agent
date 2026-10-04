from __future__ import annotations

import pytest

from app.document.content_integrity import check_content
from app.document.docx_editor import EditOperation, apply_edits
from app.document.docx_parser import parse_docx
from app.document.fidelity_evaluator import FidelityEvaluator
from app.document.fidelity_models import EditSpec, Finding, GateResult, GateStatus, RendererInfo, Severity
from app.document.location_index import locate_unique
from app.document.structure_diff import compare_structure
from app.evaluation.regressions import REGRESSIONS
from tests.conftest import LIMITS, fixture_bytes


def _edit(fixture_id: str, needle: str, original: str, new: str, edit_id: str = "e1"):
    data = fixture_bytes(fixture_id)
    index, _ = parse_docx(data)
    blk = next(b for b in index.blocks if needle in b.text)
    rng = locate_unique(blk.text, original)
    op = EditOperation(edit_id=edit_id, location_id=blk.location_id, start=rng.start, end=rng.end, expected_text=original, new_text=new)
    result = apply_edits(data, index, [op])
    assert result.ok
    spec = EditSpec(edit_id=edit_id, location_id=blk.location_id, start=rng.start, end=rng.end, original_text=original, new_text=new)
    return data, index, result.output, spec


@pytest.fixture
def edited():
    return _edit("simple_one_page", "Worked on the migration", "Worked on the migration of", "Migrated")


def test_structure_gate_passes_for_correct_edit(edited):
    data, index, output, spec = edited
    gate = compare_structure(data, output, index, {spec.location_id})
    assert gate.status == GateStatus.PASS, gate.findings
    assert gate.metrics["changed_parts"] == ["word/document.xml"]


def test_content_gate_passes_for_correct_edit(edited):
    data, index, output, spec = edited
    out_index, _ = parse_docx(output)
    gate, checks = check_content(index, out_index, [spec], [])
    assert gate.status == GateStatus.PASS, gate.findings
    assert [c.status for c in checks] == ["applied_as_approved"]


def test_content_gate_detects_missing_accepted_edit(edited):
    data, index, _, spec = edited
    gate, checks = check_content(index, index, [spec], [])
    assert gate.status == GateStatus.FAIL
    assert checks[0].status == "mismatch"


def test_content_gate_detects_rejected_edit_in_output(edited):
    data, index, output, spec = edited
    out_index, _ = parse_docx(output)
    rejected = spec.model_copy(update={"edit_id": "r1"})
    gate, _ = check_content(index, out_index, [], [rejected])
    codes = {f.code for f in gate.findings}
    assert gate.status == GateStatus.FAIL
    assert "rejected_edit_present" in codes and "unintended_text_change" in codes


def test_content_gate_flags_unsupported_numbers():
    data, index, output, spec = _edit(
        "simple_one_page", "Worked on the migration", "Worked on the migration of", "Migrated 14 services and"
    )
    out_index, _ = parse_docx(output)
    gate, _ = check_content(index, out_index, [spec], [])
    assert any(f.code == "unsupported_numeric_claim" for f in gate.findings)
    gate_with_fact, _ = check_content(index, out_index, [spec], [], user_fact_text="I migrated 14 services.")
    assert gate_with_fact.status == GateStatus.PASS


NON_VISUAL_EXPECTATIONS = {
    "font_size_change": "structure",
    "unedited_text_change": "content",
    "numeric_fabrication": "content",
    "remove_bold": "structure",
    "color_change": "structure",
    "paragraph_spacing": "structure",
    "margin_change": "structure",
    "page_break_inserted": "structure",
    "table_width_change": "structure",
    "paragraph_duplicated": "structure",
    "character_spacing": "structure",
    "truncated_package": "integrity",
    "external_relationship_added": "integrity",
    "unrelated_part_changed": "structure",
    "highlight_added": "structure",
    "cell_shading_added": "structure",
    "image_content_changed": "structure",
}


EDIT_FOR_FIXTURE = {
    "simple_one_page": ("Worked on the migration", "Worked on the migration of", "Migrated"),
    "tables": ("Maintained CI pipelines", "Maintained CI pipelines", "Maintained and improved CI pipelines"),
    "images": ("Coordinated print", "with three external vendors", "with three external print vendors"),
}
REGRESSION_FIXTURE = {
    "table_width_change": "tables",
    "image_removed": "images",
    "cell_shading_added": "tables",
    "image_content_changed": "images",
}
NON_VISUAL_EXPECTATIONS["image_removed"] = "structure"


@pytest.mark.parametrize("regression", REGRESSIONS, ids=lambda r: r.regression_id)
def test_structural_and_content_gates_block_regressions(regression):
    fixture_id = REGRESSION_FIXTURE.get(regression.regression_id, "simple_one_page")
    data, index, output, spec = _edit(fixture_id, *EDIT_FOR_FIXTURE[fixture_id])
    broken = regression.apply(output, index, {spec.location_id})
    assert broken is not None
    evaluator = FidelityEvaluator(LIMITS, renderer=None)
    report = evaluator.evaluate(data, broken, index, [spec], [])
    assert report.decision == GateStatus.FAIL
    assert report.gates[NON_VISUAL_EXPECTATIONS[regression.regression_id]].status == GateStatus.FAIL


def test_renderer_unavailable_requires_review(edited):
    data, index, output, spec = edited
    report = FidelityEvaluator(LIMITS, renderer=None).evaluate(data, output, index, [spec], [])
    assert report.gates["visual"].status == GateStatus.NOT_RUN
    assert report.decision == GateStatus.REVIEW_REQUIRED


def test_visual_gate_required_policy_fails_without_renderer(edited):
    data, index, output, spec = edited
    report = FidelityEvaluator(LIMITS, renderer=None, visual_gate_required=True).evaluate(data, output, index, [spec], [])
    assert report.decision == GateStatus.FAIL


def _gate(name, status, severity=None):
    findings = [Finding(code="x", severity=severity, message="m")] if severity else []
    return GateResult(name=name, status=status, findings=findings)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ({"integrity": "PASS", "structure": "PASS", "content": "PASS", "visual": "PASS"}, GateStatus.PASS),
        ({"integrity": "PASS", "structure": "FAIL", "content": "PASS", "visual": "PASS"}, GateStatus.FAIL),
        ({"integrity": "PASS", "structure": "PASS", "content": "PASS", "visual": "REVIEW_REQUIRED"}, GateStatus.REVIEW_REQUIRED),
        ({"integrity": "PASS", "structure": "PASS", "content": "FAIL", "visual": "REVIEW_REQUIRED"}, GateStatus.FAIL),
        ({"integrity": "PASS", "structure": "PASS", "content": "PASS", "visual": "NOT_RUN"}, GateStatus.REVIEW_REQUIRED),
    ],
)
def test_decision_policy(statuses, expected):
    sev = {"FAIL": Severity.ERROR, "REVIEW_REQUIRED": Severity.WARNING}
    gates = {k: _gate(k, GateStatus(v), sev.get(v)) for k, v in statuses.items()}
    decision, reasons = FidelityEvaluator(LIMITS, None).decide(gates, RendererInfo())
    assert decision == expected and reasons


def test_unpinned_renderer_version_requires_review():
    gates = {k: _gate(k, GateStatus.PASS) for k in ("integrity", "structure", "content", "visual")}
    evaluator = FidelityEvaluator(LIMITS, None, expected_renderer_version="24.2")
    decision, reasons = evaluator.decide(gates, RendererInfo(version="7.6.4", expected_version="24.2", pinned=False))
    assert decision == GateStatus.REVIEW_REQUIRED
    assert "differs from pinned" in reasons[0]


# ---------------------------------------------------------------- renderer-backed


@pytest.mark.renderer
def test_full_pipeline_pass_with_visual(edited, tmp_path):
    from app.document.visual_renderer import LibreOfficeRenderer

    data, index, output, spec = edited
    report = FidelityEvaluator(LIMITS, LibreOfficeRenderer()).evaluate(data, output, index, [spec], [], artifact_dir=tmp_path)
    assert report.gates["visual"].status == GateStatus.PASS, report.gates["visual"].findings
    assert report.decision == GateStatus.PASS
    assert report.page_count.original == report.page_count.output == 1
    assert report.unexpected_change_count == 0


@pytest.mark.renderer
@pytest.mark.parametrize("regression_id", ["font_size_change", "margin_change", "color_change", "page_break_inserted", "highlight_added"])
def test_visual_gate_detects_regressions_independently(edited, regression_id, tmp_path):
    from app.document.visual_renderer import LibreOfficeRenderer

    data, index, output, spec = edited
    broken = next(r for r in REGRESSIONS if r.regression_id == regression_id).apply(output, index, {spec.location_id})
    evaluator = FidelityEvaluator(LIMITS, LibreOfficeRenderer())
    analysis = evaluator.check_visual(data, broken, index, {spec.location_id}, tmp_path)
    assert analysis.gate.status != GateStatus.PASS, regression_id


@pytest.mark.renderer
def test_long_edit_reflow_across_pages_requires_review():
    from app.document.visual_renderer import LibreOfficeRenderer

    data, index, output, spec = _edit(
        "multipage",
        "Delivered feature set 1 for the Northwind",
        "coordinating with design and QA",
        "coordinating closely with the design and QA teams each sprint",
    )
    report = FidelityEvaluator(LIMITS, LibreOfficeRenderer()).evaluate(data, output, index, [spec], [])
    assert report.gates["structure"].status == GateStatus.PASS
    assert report.gates["content"].status == GateStatus.PASS
    assert report.decision == GateStatus.REVIEW_REQUIRED
    assert any("page" in w for w in report.layout_warnings)


@pytest.mark.renderer
def test_visual_gate_reflowed_table_clean_but_detects_changes_below_reflow(tmp_path):
    """Shift-tolerant comparison: legitimate reflow passes, pixel-only changes below it are caught."""
    from app.document.visual_renderer import LibreOfficeRenderer

    data, index, output, spec = _edit("tables", *EDIT_FOR_FIXTURE["tables"])
    evaluator = FidelityEvaluator(LIMITS, LibreOfficeRenderer())
    assert evaluator.check_visual(data, output, index, {spec.location_id}).gate.status == GateStatus.PASS
    for rid in ("highlight_added", "cell_shading_added"):
        broken = next(r for r in REGRESSIONS if r.regression_id == rid).apply(output, index, {spec.location_id})
        assert evaluator.check_visual(data, broken, index, {spec.location_id}).gate.status != GateStatus.PASS, rid
