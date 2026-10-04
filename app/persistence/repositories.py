"""SQLite persistence for sessions, proposals, review decisions and finalizations.

SQLite in WAL mode is safe for several worker processes on one host. All
state transitions use conditional updates (compare-and-set on status and
``review_revision``) so concurrent requests cannot interleave decisions with
finalization. A multi-host deployment should replace this module with a
server database implementing the same methods.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.errors import InvalidState, NotFound, StaleReview
from app.persistence.artifact_store import StoredArtifact
from app.resume.proposal_models import Decision, EditProposal

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    original_file_name TEXT NOT NULL,
    document_version TEXT NOT NULL,
    job_description TEXT NOT NULL,
    company_details TEXT NOT NULL,
    candidate_notes TEXT NOT NULL,
    review_revision INTEGER NOT NULL DEFAULT 0,
    analysis_json TEXT,
    error_code TEXT,
    error_message TEXT,
    lease_until REAL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    edit_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    position INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'pending',
    confirmed INTEGER NOT NULL DEFAULT 0,
    decided_at TEXT,
    PRIMARY KEY (session_id, edit_id),
    UNIQUE (session_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS finalizations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    review_revision INTEGER NOT NULL,
    decision TEXT NOT NULL,
    output_artifact_id TEXT,
    report_artifact_id TEXT,
    summary_json TEXT NOT NULL,
    acknowledged_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    edit_id TEXT,
    event TEXT NOT NULL,
    data_json TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
"""


class SessionStatus:
    ANALYZING = "analyzing"
    ANALYSIS_FAILED = "analysis_failed"
    AWAITING_REVIEW = "awaiting_review"
    FINALIZING = "finalizing"
    FINALIZED = "finalized"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@dataclass
class SessionRecord:
    id: str
    token_hash: str
    status: str
    created_at: str
    updated_at: str
    expires_at: str
    original_file_name: str
    document_version: str
    job_description: str
    company_details: str
    candidate_notes: str
    review_revision: int
    analysis: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    lease_until: float | None


@dataclass
class ProposalRecord:
    proposal: EditProposal
    decision: Decision
    confirmed: bool
    decided_at: str | None


@dataclass
class FinalizationRecord:
    id: str
    session_id: str
    idempotency_key: str
    review_revision: int
    decision: str
    output_artifact_id: str | None
    report_artifact_id: str | None
    summary: dict[str, Any]
    acknowledged_at: str | None
    created_at: str


class Repository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conn().executescript(SCHEMA)  # executescript manages its own transaction

    # ------------------------------------------------------------------ plumbing
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def ping(self) -> bool:
        self._conn().execute("SELECT 1").fetchone()
        return True

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _event(self, conn: sqlite3.Connection, session_id: str, event: str, edit_id: str | None = None, data: dict | None = None) -> None:
        conn.execute(
            "INSERT INTO events(session_id, edit_id, event, data_json, at) VALUES (?,?,?,?,?)",
            (session_id, edit_id, event, json.dumps(data or {}), _iso(_now())),
        )

    # ------------------------------------------------------------------ sessions
    def create_session(
        self,
        *,
        token_hash: str,
        original_file_name: str,
        document_version: str,
        job_description: str,
        company_details: str,
        candidate_notes: str,
        ttl_hours: int,
    ) -> SessionRecord:
        session_id = uuid.uuid4().hex
        now = _now()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO sessions(id, token_hash, status, created_at, updated_at, expires_at, original_file_name, document_version,"
                " job_description, company_details, candidate_notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    token_hash,
                    SessionStatus.ANALYZING,
                    _iso(now),
                    _iso(now),
                    _iso(now + timedelta(hours=ttl_hours)),
                    original_file_name,
                    document_version,
                    job_description,
                    company_details,
                    candidate_notes,
                ),
            )
            self._event(conn, session_id, "session_created", data={"document_version": document_version})
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        row = self._conn().execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise NotFound("Session not found.")
        data = dict(row)
        raw_analysis = data.pop("analysis_json")
        data["analysis"] = json.loads(raw_analysis) if raw_analysis else None
        return SessionRecord(**data)

    def add_artifact(self, artifact: StoredArtifact) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO artifacts(id, session_id, kind, sha256, size, created_at) VALUES (?,?,?,?,?,?)",
                (artifact.artifact_id, artifact.session_id, artifact.kind, artifact.sha256, artifact.size, _iso(_now())),
            )

    def get_artifact(self, session_id: str, artifact_id: str) -> StoredArtifact:
        row = self._conn().execute("SELECT * FROM artifacts WHERE id=? AND session_id=?", (artifact_id, session_id)).fetchone()
        if row is None:
            raise NotFound()
        return StoredArtifact(row["id"], row["session_id"], row["kind"], row["sha256"], row["size"])

    def find_artifact(self, session_id: str, kind: str) -> StoredArtifact:
        row = (
            self._conn()
            .execute("SELECT * FROM artifacts WHERE session_id=? AND kind=? ORDER BY created_at LIMIT 1", (session_id, kind))
            .fetchone()
        )
        if row is None:
            raise NotFound()
        return StoredArtifact(row["id"], row["session_id"], row["kind"], row["sha256"], row["size"])

    def claim_analysis(self, session_id: str, lease_seconds: float) -> bool:
        """Take an exclusive lease to run analysis (prevents duplicate model calls)."""
        now = time.time()
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE sessions SET status=?, lease_until=?, error_code=NULL, error_message=NULL, updated_at=? "
                "WHERE id=? AND (status=? OR (status=? AND (lease_until IS NULL OR lease_until < ?)))",
                (
                    SessionStatus.ANALYZING,
                    now + lease_seconds,
                    _iso(_now()),
                    session_id,
                    SessionStatus.ANALYSIS_FAILED,
                    SessionStatus.ANALYZING,
                    now,
                ),
            )
            if cur.rowcount:
                self._event(conn, session_id, "analysis_started")
            return cur.rowcount == 1

    def complete_analysis(self, session_id: str, analysis: dict[str, Any], proposals: list[EditProposal]) -> None:
        with self._tx() as conn:
            row = conn.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None or row["status"] != SessionStatus.ANALYZING:
                raise InvalidState("Analysis results arrived for a session that is not analyzing.")
            conn.execute("DELETE FROM proposals WHERE session_id=?", (session_id,))
            for position, p in enumerate(proposals):
                conn.execute(
                    "INSERT INTO proposals(session_id, edit_id, idempotency_key, position, payload_json) VALUES (?,?,?,?,?)",
                    (session_id, p.edit_id, p.idempotency_key, position, p.model_dump_json()),
                )
                self._event(conn, session_id, "proposal_created", p.edit_id, {"edit_type": p.edit_type.value, "risk": p.risk_level.value})
            conn.execute(
                "UPDATE sessions SET status=?, analysis_json=?, lease_until=NULL, review_revision=review_revision+1, updated_at=? WHERE id=?",
                (SessionStatus.AWAITING_REVIEW, json.dumps(analysis), _iso(_now()), session_id),
            )
            self._event(conn, session_id, "analysis_completed", data={"proposals": len(proposals)})

    def fail_analysis(self, session_id: str, code: str, message: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE sessions SET status=?, error_code=?, error_message=?, lease_until=NULL, updated_at=? WHERE id=? AND status=?",
                (SessionStatus.ANALYSIS_FAILED, code, message, _iso(_now()), session_id, SessionStatus.ANALYZING),
            )
            self._event(conn, session_id, "analysis_failed", data={"code": code})

    # ------------------------------------------------------------------ proposals and decisions
    def list_proposals(self, session_id: str) -> list[ProposalRecord]:
        rows = self._conn().execute("SELECT * FROM proposals WHERE session_id=? ORDER BY position", (session_id,)).fetchall()
        out = []
        for row in rows:
            proposal = EditProposal.model_validate_json(row["payload_json"])
            decision = Decision(row["decision"])
            proposal.decision = decision
            out.append(ProposalRecord(proposal=proposal, decision=decision, confirmed=bool(row["confirmed"]), decided_at=row["decided_at"]))
        return out

    def set_decisions(self, session_id: str, expected_revision: int, decisions: list[tuple[str, Decision, bool]]) -> int:
        """Apply decisions atomically. Returns the new review revision."""
        with self._tx() as conn:
            row = conn.execute("SELECT status, review_revision FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise NotFound("Session not found.")
            if row["status"] not in (SessionStatus.AWAITING_REVIEW, SessionStatus.FINALIZED):
                raise InvalidState(f"Decisions cannot be changed while the session is {row['status']}.")
            if row["review_revision"] != expected_revision:
                raise StaleReview()
            for edit_id, decision, confirmed in decisions:
                cur = conn.execute(
                    "UPDATE proposals SET decision=?, confirmed=?, decided_at=? WHERE session_id=? AND edit_id=?",
                    (decision.value, int(confirmed), _iso(_now()), session_id, edit_id),
                )
                if cur.rowcount != 1:
                    raise NotFound(f"Unknown edit {edit_id}.")
                self._event(conn, session_id, f"edit_{decision.value}", edit_id, {"confirmed": confirmed})
            conn.execute(
                "UPDATE sessions SET review_revision=review_revision+1, status=?, updated_at=? WHERE id=?",
                (SessionStatus.AWAITING_REVIEW, _iso(_now()), session_id),
            )
            return expected_revision + 1

    # ------------------------------------------------------------------ finalization
    def find_finalization_by_key(self, session_id: str, key: str) -> FinalizationRecord | None:
        row = self._conn().execute("SELECT * FROM finalizations WHERE session_id=? AND idempotency_key=?", (session_id, key)).fetchone()
        return self._finalization(row) if row else None

    def get_finalization(self, session_id: str, finalization_id: str) -> FinalizationRecord:
        row = self._conn().execute("SELECT * FROM finalizations WHERE id=? AND session_id=?", (finalization_id, session_id)).fetchone()
        if row is None:
            raise NotFound("Finalization not found.")
        return self._finalization(row)

    def latest_finalization(self, session_id: str) -> FinalizationRecord | None:
        row = (
            self._conn()
            .execute("SELECT * FROM finalizations WHERE session_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (session_id,))
            .fetchone()
        )
        return self._finalization(row) if row else None

    def begin_finalization(self, session_id: str, expected_revision: int, lease_seconds: float) -> None:
        now = time.time()
        with self._tx() as conn:
            row = conn.execute("SELECT status, review_revision, lease_until FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise NotFound("Session not found.")
            if row["review_revision"] != expected_revision:
                raise StaleReview()
            busy = row["status"] == SessionStatus.FINALIZING and (row["lease_until"] or 0) > now
            if busy:
                raise InvalidState("Finalization is already running for this session.")
            if row["status"] not in (SessionStatus.AWAITING_REVIEW, SessionStatus.FINALIZED, SessionStatus.FINALIZING):
                raise InvalidState(f"The session cannot be finalized while it is {row['status']}.")
            conn.execute(
                "UPDATE sessions SET status=?, lease_until=?, updated_at=? WHERE id=?",
                (SessionStatus.FINALIZING, now + lease_seconds, _iso(_now()), session_id),
            )
            self._event(conn, session_id, "finalization_started", data={"review_revision": expected_revision})

    def complete_finalization(
        self,
        session_id: str,
        *,
        key: str,
        review_revision: int,
        decision: str,
        output_artifact_id: str | None,
        report_artifact_id: str | None,
        summary: dict[str, Any],
        applied_edit_ids: list[str],
    ) -> FinalizationRecord:
        fid = uuid.uuid4().hex
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO finalizations(id, session_id, idempotency_key, review_revision, decision, output_artifact_id, report_artifact_id,"
                " summary_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    fid,
                    session_id,
                    key,
                    review_revision,
                    decision,
                    output_artifact_id,
                    report_artifact_id,
                    json.dumps(summary),
                    _iso(_now()),
                ),
            )
            for edit_id in applied_edit_ids:
                self._event(conn, session_id, "edit_applied", edit_id, {"finalization_id": fid})
            self._event(conn, session_id, "finalization_completed", data={"finalization_id": fid, "decision": decision})
            conn.execute(
                "UPDATE sessions SET status=?, lease_until=NULL, updated_at=? WHERE id=?",
                (SessionStatus.FINALIZED if decision != "FAIL" else SessionStatus.AWAITING_REVIEW, _iso(_now()), session_id),
            )
        return self.get_finalization(session_id, fid)

    def abort_finalization(self, session_id: str, reason: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE sessions SET status=?, lease_until=NULL, updated_at=? WHERE id=? AND status=?",
                (SessionStatus.AWAITING_REVIEW, _iso(_now()), session_id, SessionStatus.FINALIZING),
            )
            self._event(conn, session_id, "finalization_aborted", data={"reason": reason})

    def acknowledge(self, session_id: str, finalization_id: str) -> FinalizationRecord:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE finalizations SET acknowledged_at=? WHERE id=? AND session_id=? AND decision='REVIEW_REQUIRED'",
                (_iso(_now()), finalization_id, session_id),
            )
            if cur.rowcount != 1:
                raise InvalidState("Only results that require review can be acknowledged.")
            self._event(conn, session_id, "review_warnings_acknowledged", data={"finalization_id": finalization_id})
        return self.get_finalization(session_id, finalization_id)

    def record_event(self, session_id: str, event: str, edit_id: str | None = None, data: dict | None = None) -> None:
        with self._tx() as conn:
            self._event(conn, session_id, event, edit_id, data)

    def events(self, session_id: str) -> list[dict[str, Any]]:
        rows = (
            self._conn()
            .execute("SELECT edit_id, event, data_json, at FROM events WHERE session_id=? ORDER BY id", (session_id,))
            .fetchall()
        )
        return [{"edit_id": r["edit_id"], "event": r["event"], "data": json.loads(r["data_json"] or "{}"), "at": r["at"]} for r in rows]

    # ------------------------------------------------------------------ retention
    def expired_session_ids(self) -> list[str]:
        rows = self._conn().execute("SELECT id FROM sessions WHERE expires_at < ?", (_iso(_now()),)).fetchall()
        return [r["id"] for r in rows]

    def delete_session(self, session_id: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))

    @staticmethod
    def _finalization(row: sqlite3.Row) -> FinalizationRecord:
        data = dict(row)
        data["summary"] = json.loads(data.pop("summary_json"))
        return FinalizationRecord(**data)
