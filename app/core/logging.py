"""Structured JSON logging with correlation IDs and content redaction.

Resume and job-description text must never reach logs unless
``RESUME_LOG_CONTENT=true`` is explicitly configured for local debugging.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Keys whose values are user content and are redacted unless content logging is enabled.
CONTENT_KEYS = frozenset(
    {
        "text",
        "original_text",
        "proposed_text",
        "resume_text",
        "job_description",
        "company_details",
        "candidate_notes",
        "candidate_name",
        "prompt",
        "response",
        "evidence",
        "file_name",
    }
)

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, log_content: bool = False) -> None:
        super().__init__()
        self.log_content = log_content

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        cid = correlation_id_var.get()
        if cid:
            payload["correlation_id"] = cid
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = redact(key, value, self.log_content)
        if record.exc_info:
            # Exception type only; tracebacks may contain user content in locals/messages.
            exc_type = record.exc_info[0]
            payload["exception"] = exc_type.__name__ if exc_type else "Exception"
        return json.dumps(payload, default=str)


def redact(key: str, value: Any, log_content: bool) -> Any:
    if log_content:
        return value
    if key in CONTENT_KEYS:
        if isinstance(value, str):
            return f"<redacted:{len(value)} chars>"
        return "<redacted>"
    if isinstance(value, dict):
        return {k: redact(k, v, log_content) for k, v in value.items()}
    return value


def configure_logging(level: str = "INFO", log_content: bool = False) -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(log_content=log_content))
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Uvicorn access logs include query strings (download tokens); keep them quiet.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
