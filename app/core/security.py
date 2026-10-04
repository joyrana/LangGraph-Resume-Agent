"""Session capability tokens and signed, expiring download tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from app.core.errors import Unauthorized


def new_session_token() -> str:
    """Bearer capability for a single session. Only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(token: str | None, expected_hash: str) -> bool:
    if not token:
        return False
    return hmac.compare_digest(hash_token(token), expected_hash)


@dataclass(frozen=True)
class DownloadClaim:
    session_id: str
    finalization_id: str
    expires_at: int


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_download_token(secret: str, session_id: str, finalization_id: str, ttl_seconds: int, now: float | None = None) -> tuple[str, int]:
    expires_at = int((time.time() if now is None else now) + ttl_seconds)
    body = _b64(json.dumps({"s": session_id, "f": finalization_id, "e": expires_at}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", expires_at


def verify_download_token(secret: str, token: str, now: float | None = None) -> DownloadClaim:
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise ValueError("signature")
        data = json.loads(_unb64(body))
        claim = DownloadClaim(session_id=str(data["s"]), finalization_id=str(data["f"]), expires_at=int(data["e"]))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise Unauthorized("The download link is invalid.") from exc
    if claim.expires_at < (time.time() if now is None else now):
        raise Unauthorized("The download link has expired. Request a new one.")
    return claim
