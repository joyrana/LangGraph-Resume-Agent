"""Filesystem artifact store with generated ids and integrity checks.

Clients never supply paths: artifacts are addressed by server-generated ids
and the session that owns them. Every read verifies the stored SHA-256, and
the original upload is written read-only.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import AppError, NotFound

_ID = re.compile(r"^[a-f0-9]{32}$")


class ArtifactIntegrityError(AppError):
    code = "artifact_integrity_error"
    status_code = 500
    default_message = "A stored document failed its integrity check."


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: str
    session_id: str
    kind: str
    sha256: str
    size: int


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, session_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise NotFound()
        path = (self.root / session_id).resolve()
        if path.parent != self.root:
            raise NotFound()
        return path

    def put(self, session_id: str, kind: str, data: bytes, *, read_only: bool = False) -> StoredArtifact:
        directory = self._dir(session_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        artifact_id = uuid.uuid4().hex
        final = directory / artifact_id
        tmp = directory / f".{artifact_id}.tmp"
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, final)
        os.chmod(final, 0o400 if read_only else 0o600)
        return StoredArtifact(artifact_id, session_id, kind, hashlib.sha256(data).hexdigest(), len(data))

    def get(self, artifact: StoredArtifact) -> bytes:
        if not _ID.match(artifact.artifact_id):
            raise NotFound()
        path = self._dir(artifact.session_id) / artifact.artifact_id
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise NotFound("The stored document is no longer available.") from exc
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ArtifactIntegrityError()
        return data

    def work_dir(self, session_id: str, name: str) -> Path:
        if not re.fullmatch(r"[a-z0-9_]{1,40}", name):
            raise ValueError("invalid work dir name")
        path = self._dir(session_id) / "work" / name
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def delete_session(self, session_id: str) -> None:
        directory = self._dir(session_id)
        if directory.exists():
            for child in directory.rglob("*"):
                if child.is_file():
                    os.chmod(child, 0o600)
            shutil.rmtree(directory, ignore_errors=True)
