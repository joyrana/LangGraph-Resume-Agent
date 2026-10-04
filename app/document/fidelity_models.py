"""Report models shared by all fidelity gates."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GateStatus(str, Enum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"  # requires human review
    ERROR = "error"  # blocks delivery


class Finding(BaseModel):
    code: str
    severity: Severity
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class GateResult(BaseModel):
    name: str
    status: GateStatus
    findings: list[Finding] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_findings(cls, name: str, findings: list[Finding], metrics: dict[str, Any] | None = None) -> GateResult:
        if any(f.severity == Severity.ERROR for f in findings):
            status = GateStatus.FAIL
        elif any(f.severity == Severity.WARNING for f in findings):
            status = GateStatus.REVIEW_REQUIRED
        else:
            status = GateStatus.PASS
        return cls(name=name, status=status, findings=findings, metrics=metrics or {})


class AcceptedEditCheck(BaseModel):
    edit_id: str
    status: str  # "applied_as_approved" | "mismatch" | "missing"
    message: str | None = None


class EditSpec(BaseModel):
    """An approved or rejected edit, as recorded at review time."""

    edit_id: str
    location_id: str
    start: int
    end: int
    original_text: str
    new_text: str


class PageCount(BaseModel):
    original: int | None = None
    output: int | None = None

    @property
    def delta(self) -> int | None:
        if self.original is None or self.output is None:
            return None
        return self.output - self.original


class RendererInfo(BaseModel):
    name: str = "libreoffice"
    version: str | None = None
    expected_version: str | None = None
    pinned: bool = False
    dpi: int | None = None


class FidelityReport(BaseModel):
    evaluator_version: str
    decision: GateStatus
    reasons: list[str]
    gates: dict[str, GateResult]
    accepted_edit_verification: list[AcceptedEditCheck]
    unexpected_change_count: int
    page_count: PageCount
    page_count_delta: int | None = None
    layout_warnings: list[str]
    unsupported_features: list[str]
    renderer: RendererInfo
    artifacts: dict[str, str] = Field(default_factory=dict)
