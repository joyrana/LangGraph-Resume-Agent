"""Analysis progress: step states derived from recorded events."""

from __future__ import annotations

from app.workflow.progress import PROGRESS_EVENT, analysis_progress


def _ev(event: str, step: str | None = None, detail: str = "", at: str = "t") -> dict:
    return {"event": event, "data": {"step": step, "detail": detail} if step else {}, "at": at, "edit_id": None}


def _states(progress) -> dict[str, str]:
    return {s.id: s.state for s in progress.steps}


def test_first_step_is_active_before_any_progress_event():
    progress = analysis_progress([_ev("analysis_started", at="t0")], "analyzing")
    assert progress.started_at == "t0"
    assert _states(progress)["read_resume"] == "active"
    assert all(s.state == "pending" for s in progress.steps[1:])


def test_steps_before_the_current_one_are_done_and_latest_detail_wins():
    events = [
        _ev("analysis_started"),
        _ev(PROGRESS_EVENT, "read_resume", "Read 12 paragraphs"),
        _ev(PROGRESS_EVENT, "ask_model", "Waiting for q", at="t1"),
        _ev(PROGRESS_EVENT, "ask_model", "asking the model to fix them", at="t2"),
    ]
    progress = analysis_progress(events, "analyzing")
    states = _states(progress)
    assert states["read_resume"] == "done" and states["ask_model"] == "active" and states["check_evidence"] == "pending"
    ask = next(s for s in progress.steps if s.id == "ask_model")
    assert ask.detail == "asking the model to fix them" and ask.started_at == "t1"


def test_only_the_latest_run_counts_and_failure_marks_the_step():
    events = [
        _ev("analysis_started"),
        _ev(PROGRESS_EVENT, "check_evidence"),
        _ev("analysis_failed"),
        _ev("analysis_started", at="retry"),
        _ev(PROGRESS_EVENT, "read_resume"),
        _ev(PROGRESS_EVENT, "ask_model"),
    ]
    progress = analysis_progress(events, "analysis_failed")
    assert progress.started_at == "retry"
    assert _states(progress)["ask_model"] == "failed" and _states(progress)["check_evidence"] == "pending"


def test_no_progress_once_analysis_is_finished():
    assert analysis_progress([_ev("analysis_started")], "awaiting_review") is None
