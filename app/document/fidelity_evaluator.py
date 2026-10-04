"""Fidelity evaluation pipeline and delivery decision (gates A–E).

Delivery policy (Gate E), documented in docs/FIDELITY_EVALUATION.md:

* Any gate ``FAIL``                          → ``FAIL`` (download blocked, no override).
* Visual gate not run (renderer unavailable) → ``REVIEW_REQUIRED``, or ``FAIL`` when
  ``RESUME_VISUAL_GATE_REQUIRED=true``.
* Renderer version differs from the pinned version → ``REVIEW_REQUIRED``.
* Any gate ``REVIEW_REQUIRED``               → ``REVIEW_REQUIRED`` (download only after the
  user explicitly acknowledges the listed warnings).
* Otherwise                                  → ``PASS``.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from app.core.errors import RendererUnavailable, UploadRejected
from app.document.content_integrity import check_content
from app.document.document_models import DocumentIndex
from app.document.docx_parser import parse_docx
from app.document.fidelity_models import (
    AcceptedEditCheck,
    EditSpec,
    FidelityReport,
    Finding,
    GateResult,
    GateStatus,
    PageCount,
    RendererInfo,
    Severity,
)
from app.document.structure_diff import compare_structure
from app.document.upload_validator import UploadLimits, validate_docx_upload
from app.document.visual_diff import VisualAnalysis, VisualThresholds, analyze_visual, make_marker_copy
from app.document.visual_renderer import LibreOfficeRenderer

EVALUATOR_VERSION = "fidelity/1.0.0"


class FidelityEvaluator:
    def __init__(
        self,
        limits: UploadLimits,
        renderer: LibreOfficeRenderer | None,
        thresholds: VisualThresholds = VisualThresholds(),
        expected_renderer_version: str | None = None,
        visual_gate_required: bool = False,
    ) -> None:
        self.limits = limits
        self.renderer = renderer
        self.thresholds = thresholds
        self.expected_renderer_version = expected_renderer_version
        self.visual_gate_required = visual_gate_required

    # ------------------------------------------------------------------ gate A
    def check_integrity(self, original: bytes, output: bytes) -> tuple[GateResult, DocumentIndex | None]:
        findings: list[Finding] = []
        out_index: DocumentIndex | None = None
        try:
            src_report = validate_docx_upload(original, "original.docx", "", self.limits)
            out_report = validate_docx_upload(output, "output.docx", "", self.limits)
        except UploadRejected as exc:
            findings.append(
                Finding(
                    code="package_invalid",
                    severity=Severity.ERROR,
                    message=f"Output package failed validation: {exc.message}",
                    details=exc.details,
                )
            )
            return GateResult.from_findings("integrity", findings), None
        if out_report.external_hyperlinks != src_report.external_hyperlinks:
            findings.append(
                Finding(code="external_relationships_changed", severity=Severity.ERROR, message="External links were added or removed.")
            )
        if out_report.main_part != src_report.main_part:
            findings.append(Finding(code="main_part_changed", severity=Severity.ERROR, message="The main document part changed."))
        try:
            out_index, _ = parse_docx(output)
            from docx import Document

            Document(io.BytesIO(output))
        except Exception as exc:
            findings.append(
                Finding(code="output_unreadable", severity=Severity.ERROR, message=f"Output cannot be reopened ({type(exc).__name__}).")
            )
            out_index = None
        try:
            import zipfile

            with zipfile.ZipFile(io.BytesIO(original)) as a, zipfile.ZipFile(io.BytesIO(output)) as b:
                missing = sorted(set(a.namelist()) - set(b.namelist()))
                if b.testzip() is not None:
                    findings.append(Finding(code="crc_error", severity=Severity.ERROR, message="Output package has a corrupt member."))
            if missing:
                findings.append(
                    Finding(
                        code="parts_missing",
                        severity=Severity.ERROR,
                        message="Parts present in the original are missing.",
                        details={"parts": missing},
                    )
                )
        except Exception as exc:  # pragma: no cover
            findings.append(
                Finding(code="package_unreadable", severity=Severity.ERROR, message=f"Package unreadable ({type(exc).__name__}).")
            )
        return GateResult.from_findings("integrity", findings), out_index

    # ------------------------------------------------------------------ gate D
    def renderer_info(self) -> RendererInfo:
        version = self.renderer.version() if self.renderer and self.renderer.available() else None
        pinned = bool(self.expected_renderer_version and version and version.startswith(self.expected_renderer_version))
        return RendererInfo(
            version=version,
            expected_version=self.expected_renderer_version,
            pinned=pinned,
            dpi=self.renderer.dpi if self.renderer else None,
        )

    def check_visual(
        self,
        original: bytes,
        output: bytes,
        original_index: DocumentIndex,
        edited_location_ids: set[str],
        artifact_dir: Path | None = None,
    ) -> VisualAnalysis:
        if self.renderer is None or not self.renderer.available():
            gate = GateResult(
                name="visual",
                status=GateStatus.NOT_RUN,
                findings=[
                    Finding(
                        code="renderer_unavailable",
                        severity=Severity.WARNING,
                        message="Visual comparison could not run because LibreOffice is unavailable.",
                    )
                ],
            )
            return VisualAnalysis(gate=gate, page_count=PageCount(), layout_warnings=[], unexpected_regions=0, artifacts={})
        docs = {
            "original": original,
            "output": output,
            "original_marked": make_marker_copy(original, original_index, edited_location_ids),
            "output_marked": make_marker_copy(output, original_index, edited_location_ids),
        }
        with tempfile.TemporaryDirectory(prefix="render_") as tmp:
            try:
                rendered = self.renderer.render(docs, Path(tmp))
            except RendererUnavailable as exc:
                gate = GateResult(
                    name="visual",
                    status=GateStatus.NOT_RUN,
                    findings=[Finding(code="renderer_failed", severity=Severity.WARNING, message=exc.message)],
                )
                return VisualAnalysis(gate=gate, page_count=PageCount(), layout_warnings=[], unexpected_regions=0, artifacts={})
            if "original" in rendered and "output" not in rendered:
                gate = GateResult(
                    name="visual",
                    status=GateStatus.FAIL,
                    findings=[
                        Finding(code="output_render_failed", severity=Severity.ERROR, message="The edited document could not be rendered.")
                    ],
                )
                return VisualAnalysis(gate=gate, page_count=PageCount(), layout_warnings=[], unexpected_regions=0, artifacts={})
            if set(rendered) != set(docs):
                gate = GateResult(
                    name="visual",
                    status=GateStatus.NOT_RUN,
                    findings=[Finding(code="renderer_failed", severity=Severity.WARNING, message="One or more comparison renders failed.")],
                )
                return VisualAnalysis(gate=gate, page_count=PageCount(), layout_warnings=[], unexpected_regions=0, artifacts={})
            return analyze_visual(
                rendered["original"].pdf_path,
                rendered["output"].pdf_path,
                rendered["original_marked"].pdf_path,
                rendered["output_marked"].pdf_path,
                rendered["original"].page_images,
                rendered["output"].page_images,
                self.renderer.dpi,
                self.thresholds,
                artifact_dir,
            )

    # ------------------------------------------------------------------ pipeline
    def evaluate(
        self,
        original: bytes,
        output: bytes,
        original_index: DocumentIndex,
        accepted: list[EditSpec],
        rejected: list[EditSpec],
        user_fact_text: str = "",
        artifact_dir: Path | None = None,
        run_visual_after_failure: bool = False,
    ) -> FidelityReport:
        edited_ids = {e.location_id for e in accepted}
        gates: dict[str, GateResult] = {}
        checks: list[AcceptedEditCheck] = []

        integrity, out_index = self.check_integrity(original, output)
        gates["integrity"] = integrity
        if integrity.status == GateStatus.FAIL or out_index is None:
            for name in ("structure", "content", "visual"):
                gates[name] = GateResult(name=name, status=GateStatus.NOT_RUN)
            visual = None
        else:
            gates["structure"] = compare_structure(original, output, original_index, edited_ids)
            gates["content"], checks = check_content(original_index, out_index, accepted, rejected, user_fact_text)
            hard_failure = any(gates[g].status == GateStatus.FAIL for g in ("structure", "content"))
            if hard_failure and not run_visual_after_failure:
                gates["visual"] = GateResult(
                    name="visual",
                    status=GateStatus.NOT_RUN,
                    findings=[
                        Finding(code="skipped_after_failure", severity=Severity.INFO, message="Skipped because an earlier gate failed.")
                    ],
                )
                visual = None
            else:
                visual = self.check_visual(original, output, original_index, edited_ids, artifact_dir)
                gates["visual"] = visual.gate

        renderer = self.renderer_info()
        decision, reasons = self.decide(gates, renderer)
        unexpected = sum(
            1 for g in ("integrity", "structure", "content") for f in gates[g].findings if f.severity in (Severity.ERROR, Severity.WARNING)
        ) + (visual.unexpected_regions if visual else 0)
        page_count = visual.page_count if visual else PageCount()
        return FidelityReport(
            evaluator_version=EVALUATOR_VERSION,
            decision=decision,
            reasons=reasons,
            gates=gates,
            accepted_edit_verification=checks,
            unexpected_change_count=unexpected,
            page_count=page_count,
            page_count_delta=page_count.delta,
            layout_warnings=visual.layout_warnings if visual else [],
            unsupported_features=list(original_index.limitations),
            renderer=renderer,
            artifacts=visual.artifacts if visual else {},
        )

    def decide(self, gates: dict[str, GateResult], renderer: RendererInfo) -> tuple[GateStatus, list[str]]:
        reasons: list[str] = []
        failed = [g for g in gates.values() if g.status == GateStatus.FAIL]
        if failed:
            for g in failed:
                reasons.extend(f"{g.name}: {f.message}" for f in g.findings if f.severity == Severity.ERROR)
            return GateStatus.FAIL, reasons
        visual = gates.get("visual")
        if visual is not None and visual.status == GateStatus.NOT_RUN:
            reasons.append("visual: layout could not be verified (renderer unavailable).")
            if self.visual_gate_required:
                return GateStatus.FAIL, reasons
        if self.expected_renderer_version and not renderer.pinned and visual is not None and visual.status != GateStatus.NOT_RUN:
            reasons.append(
                f"visual: renderer version {renderer.version} differs from pinned {self.expected_renderer_version}; thresholds may not apply."
            )
        for g in gates.values():
            if g.status == GateStatus.REVIEW_REQUIRED:
                reasons.extend(f"{g.name}: {f.message}" for f in g.findings if f.severity == Severity.WARNING)
        if reasons:
            return GateStatus.REVIEW_REQUIRED, reasons
        return GateStatus.PASS, ["All structural, content and visual checks passed."]
