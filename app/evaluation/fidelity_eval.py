"""Fidelity evaluator calibration and detection-rate measurement.

Usage::

    python -m app.evaluation.fidelity_eval --out evaluation/reports

Cases
-----
* **Controlled edits** (negatives): every ``ControlledEdit`` in the corpus,
  applied with the real editor. Expected decision comes from the manifest.
* **Injected regressions** (positives): each regression in
  ``app.evaluation.regressions`` applied on top of a correctly edited output.

Metrics
-------
* end-to-end decision: false-positive rate (good edits flagged beyond their
  expected decision) and false-negative rate (regressions delivered as PASS);
* visual gate alone (structural/content gates bypassed): detection rate per
  regression and false-positive rate on controlled edits, for every threshold
  combination in the sweep. The defaults in ``VisualThresholds`` are chosen
  from this sweep, not invented.
"""

from __future__ import annotations

import argparse
import itertools
import json
import platform
import shutil
import tempfile
import time
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.document import visual_diff
from app.document.docx_editor import EditOperation, apply_edits
from app.document.docx_parser import parse_docx
from app.document.fidelity_evaluator import EVALUATOR_VERSION, FidelityEvaluator
from app.document.fidelity_models import EditSpec, GateStatus
from app.document.location_index import locate_unique
from app.document.upload_validator import UploadLimits
from app.document.visual_diff import VisualThresholds, analyze_visual, make_marker_copy
from app.document.visual_renderer import LibreOfficeRenderer
from app.evaluation.corpus import CORPUS_VERSION, FIXTURES, ControlledEdit
from app.evaluation.regressions import REGRESSIONS

LIMITS = UploadLimits(5 * 1024 * 1024, 50 * 1024 * 1024, 500, 100.0)
SEVERITY = {"PASS": 0, "NOT_RUN": 1, "REVIEW_REQUIRED": 1, "FAIL": 2}


def build_edit(fx_data: bytes, edit: ControlledEdit, edit_id: str = "e0"):
    index, _ = parse_docx(fx_data)
    blocks = [b for b in index.blocks if edit.locate in b.text]
    if len(blocks) != 1:
        raise ValueError(f"locator '{edit.locate}' matched {len(blocks)} blocks")
    blk = blocks[0]
    rng = locate_unique(blk.text, edit.original_text)
    op = EditOperation(
        edit_id=edit_id, location_id=blk.location_id, start=rng.start, end=rng.end, expected_text=edit.original_text, new_text=edit.new_text
    )
    result = apply_edits(fx_data, index, [op])
    if not result.ok:
        raise ValueError(f"controlled edit failed: {result.failures}")
    spec = EditSpec(
        edit_id=edit_id, location_id=blk.location_id, start=rng.start, end=rng.end, original_text=edit.original_text, new_text=edit.new_text
    )
    return index, result.output, spec


def _marked_docs(prefix: str, original: bytes, output: bytes, index, edited: set[str]) -> dict[str, bytes]:
    return {
        f"{prefix}_original": original,
        f"{prefix}_output": output,
        f"{prefix}_original_marked": make_marker_copy(original, index, edited),
        f"{prefix}_output_marked": make_marker_copy(output, index, edited),
    }


def _sweep_configs() -> list[VisualThresholds]:
    configs = []
    for tol, px, area in itertools.product([0.25, 0.75, 1.5], [32, 48, 96], [4, 16, 64]):
        configs.append(VisualThresholds(position_tolerance_pt=tol, pixel_threshold=px, min_region_area_px=area))
    return configs


def run(out_dir: Path) -> dict:
    renderer = LibreOfficeRenderer()
    if not renderer.available():
        raise SystemExit("LibreOffice/pdftoppm not available: visual calibration cannot run.")
    evaluator = FidelityEvaluator(LIMITS, renderer, expected_renderer_version=renderer.version())

    # Cache PDF text extraction across the threshold sweep.
    original_extract = visual_diff.extract_layout
    visual_diff.extract_layout = lru_cache(maxsize=None)(original_extract)  # type: ignore[assignment]

    started = time.time()
    tmp_root = Path(tempfile.mkdtemp(prefix="fidelity_eval_"))
    good_cases: list[dict] = []
    regression_cases: list[dict] = []
    visual_inputs: list[tuple[str, str, bool, dict]] = []  # (case_id, kind, expect_flag, renders)
    no_visual = FidelityEvaluator(LIMITS, None)
    renderer_info = evaluator.renderer_info()

    def visual_gate(r: dict, cfg: VisualThresholds = VisualThresholds()):
        return analyze_visual(
            r["original"].pdf_path,
            r["output"].pdf_path,
            r["original_marked"].pdf_path,
            r["output_marked"].pdf_path,
            r["original"].page_images,
            r["output"].page_images,
            renderer.dpi,
            cfg,
        ).gate

    try:
        for fixture in FIXTURES:
            print(f"[{time.time() - started:6.1f}s] fixture {fixture.fixture_id}", flush=True)
            data = fixture.build()
            pending: list[dict[str, Any]] = []  # cases of this fixture awaiting a batched render
            batch: dict[str, bytes] = {}
            for i, edit in enumerate(fixture.edits):
                index, output, spec = build_edit(data, edit, f"{fixture.fixture_id}-{i}")
                prefix = f"c{len(pending)}"
                batch.update(_marked_docs(prefix, data, output, index, {spec.location_id}))
                pending.append(
                    {
                        "prefix": prefix,
                        "kind": "controlled_edit",
                        "case": f"{fixture.fixture_id}/edit{i}",
                        "edit": edit,
                        "report": no_visual.evaluate(data, output, index, [spec], []),
                    }
                )
                if i != 0:
                    continue
                for reg in REGRESSIONS:
                    broken = reg.apply(output, index, {spec.location_id})
                    if broken is None:
                        continue
                    rep = no_visual.evaluate(data, broken, index, [spec], [], run_visual_after_failure=True)
                    entry: dict[str, Any] = {
                        "kind": reg.regression_id,
                        "case": f"{fixture.fixture_id}/{reg.regression_id}",
                        "reg": reg,
                        "report": rep,
                        "prefix": None,
                    }
                    if rep.gates["integrity"].status != GateStatus.FAIL:
                        entry["prefix"] = f"c{len(pending)}"
                        batch.update(_marked_docs(entry["prefix"], data, broken, index, {spec.location_id}))
                    pending.append(entry)

            rendered = renderer.render(batch, tmp_root / fixture.fixture_id)
            for entry in pending:
                renders = None
                if entry["prefix"]:
                    keys = {k: f"{entry['prefix']}_{k}" for k in ("original", "output", "original_marked", "output_marked")}
                    if all(v in rendered for v in keys.values()):
                        renders = {k: rendered[v] for k, v in keys.items()}
                gates = dict(entry["report"].gates)
                if renders is not None and gates["integrity"].status != GateStatus.FAIL:
                    gates["visual"] = visual_gate(renders)
                decision, _ = evaluator.decide(gates, renderer_info)
                findings = [f.code for g in gates.values() for f in g.findings]
                if entry["kind"] == "controlled_edit":
                    edit = entry["edit"]
                    good_cases.append(
                        {
                            "case": entry["case"],
                            "note": edit.note,
                            "expected": edit.expected,
                            "decision": decision.value,
                            "visual": gates["visual"].status.value,
                            "findings": findings,
                            "correct": decision.value == edit.expected,
                        }
                    )
                    if renders:
                        visual_inputs.append((entry["case"], "controlled_edit", edit.expected != "PASS", renders))
                else:
                    reg = entry["reg"]
                    regression_cases.append(
                        {
                            "case": entry["case"],
                            "regression": reg.regression_id,
                            "expected_min": reg.expected,
                            "decision": decision.value,
                            "detected": decision != GateStatus.PASS,
                            "severity_met": SEVERITY[decision.value] >= SEVERITY[reg.expected],
                            "gates": {k: g.status.value for k, g in gates.items()},
                            "findings": findings,
                        }
                    )
                    if renders and reg.visual_detectable:
                        visual_inputs.append((entry["case"], reg.regression_id, True, renders))

        # ---- visual-only threshold sweep over cached renders
        sweep: list[dict] = []
        for n_cfg, cfg in enumerate(_sweep_configs()):
            print(
                f"[{time.time() - started:6.1f}s] sweep config {n_cfg + 1}/{len(_sweep_configs())} over {len(visual_inputs)} cases",
                flush=True,
            )
            tp = fn = fp = tn = 0
            missed: list[str] = []
            false_alarms: list[str] = []
            for case_id, kind, expect_flag, r in visual_inputs:
                analysis = analyze_visual(
                    r["original"].pdf_path,
                    r["output"].pdf_path,
                    r["original_marked"].pdf_path,
                    r["output_marked"].pdf_path,
                    r["original"].page_images,
                    r["output"].page_images,
                    renderer.dpi,
                    cfg,
                    compute_ssim=False,
                )
                flagged = analysis.gate.status != GateStatus.PASS
                if kind == "controlled_edit":
                    if expect_flag:
                        tp, fn = (tp + 1, fn) if flagged else (tp, fn + 1)
                        if not flagged:
                            missed.append(case_id)
                    else:
                        fp, tn = (fp + 1, tn) if flagged else (fp, tn + 1)
                        if flagged:
                            false_alarms.append(case_id)
                else:
                    tp, fn = (tp + 1, fn) if flagged else (tp, fn + 1)
                    if not flagged:
                        missed.append(case_id)
            sweep.append(
                {
                    "thresholds": asdict(cfg),
                    "true_positives": tp,
                    "false_negatives": fn,
                    "false_positives": fp,
                    "true_negatives": tn,
                    "false_negative_rate": round(fn / max(1, tp + fn), 4),
                    "false_positive_rate": round(fp / max(1, fp + tn), 4),
                    "missed": missed,
                    "false_alarms": false_alarms,
                }
            )
    finally:
        visual_diff.extract_layout = original_extract  # type: ignore[assignment]
        shutil.rmtree(tmp_root, ignore_errors=True)

    defaults = asdict(VisualThresholds())
    default_row = next(row for row in sweep if row["thresholds"] == defaults)
    good_expected_pass = [c for c in good_cases if c["expected"] == "PASS"]
    report = {
        "evaluator_version": EVALUATOR_VERSION,
        "corpus_version": CORPUS_VERSION,
        "renderer": {"name": "libreoffice", "version": renderer.version(), "dpi": renderer.dpi},
        "platform": platform.platform(),
        "runtime_seconds": round(time.time() - started, 1),
        "end_to_end": {
            "controlled_edits": len(good_cases),
            "controlled_edits_correct": sum(c["correct"] for c in good_cases),
            "false_positive_rate": round(sum(c["decision"] != "PASS" for c in good_expected_pass) / max(1, len(good_expected_pass)), 4),
            "regressions": len(regression_cases),
            "regressions_detected": sum(c["detected"] for c in regression_cases),
            "false_negative_rate": round(sum(not c["detected"] for c in regression_cases) / max(1, len(regression_cases)), 4),
            "severity_met_rate": round(sum(c["severity_met"] for c in regression_cases) / max(1, len(regression_cases)), 4),
        },
        "visual_gate_only": {
            "cases": len(visual_inputs),
            "default_thresholds": default_row,
            "sweep": sweep,
        },
        "controlled_edit_cases": good_cases,
        "regression_cases": regression_cases,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "fidelity_eval.json").write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("evaluation/reports"))
    args = parser.parse_args()
    report = run(args.out)
    e2e = report["end_to_end"]
    vis = report["visual_gate_only"]["default_thresholds"]
    print(
        json.dumps(
            {
                "end_to_end": e2e,
                "visual_default": {k: vis[k] for k in ("false_negative_rate", "false_positive_rate", "missed", "false_alarms")},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
