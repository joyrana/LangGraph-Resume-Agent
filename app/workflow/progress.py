"""Human-readable analysis progress, derived from ``analysis_progress`` events.

Nodes record an event as each step starts; the session view turns the events
since the latest ``analysis_started`` into an ordered step list. Progress is
reported, never estimated: there is no percentage, only the step that is
actually running.
"""

from __future__ import annotations

from typing import Any, Literal

from app.api.schemas import AnalysisProgress, ProgressStep

PROGRESS_EVENT = "analysis_progress"

ANALYSIS_STEPS: list[tuple[str, str]] = [
    ("read_resume", "Reading your resume"),
    ("ask_model", "Asking the AI model for suggestions"),
    ("check_evidence", "Checking each suggestion against your resume"),
    ("estimate_coverage", "Estimating keyword coverage for the job"),
    ("save", "Preparing your review"),
]
_ORDER = {step: i for i, (step, _) in enumerate(ANALYSIS_STEPS)}


def analysis_progress(events: list[dict[str, Any]], status: str) -> AnalysisProgress | None:
    """Step list for an analyzing (or failed) session; None once analysis has finished."""
    if status not in ("analyzing", "analysis_failed"):
        return None
    starts = [i for i, e in enumerate(events) if e["event"] == "analysis_started"]
    if not starts:
        return None
    started = events[starts[-1]]
    reached: dict[str, dict[str, Any]] = {}
    for event in events[starts[-1] + 1 :]:
        step = event["data"].get("step")
        if event["event"] == PROGRESS_EVENT and step in _ORDER:
            # Keep the first start time, but the latest detail (e.g. a re-ask).
            reached[step] = {"at": reached.get(step, {}).get("at", event["at"]), "detail": event["data"].get("detail")}
    current = max(reached, key=_ORDER.__getitem__) if reached else ANALYSIS_STEPS[0][0]
    steps = []
    for step, label in ANALYSIS_STEPS:
        state: Literal["done", "active", "pending", "failed"]
        if _ORDER[step] < _ORDER[current]:
            state = "done"
        elif step == current:
            state = "failed" if status == "analysis_failed" else "active"
        else:
            state = "pending"
        info = reached.get(step, {})
        steps.append(ProgressStep(id=step, label=label, state=state, detail=info.get("detail"), started_at=info.get("at")))
    return AnalysisProgress(started_at=started["at"], steps=steps)
