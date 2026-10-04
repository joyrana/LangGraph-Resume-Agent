"""Structured application errors.

Every error carries a stable machine-readable ``code``, an HTTP status and a
user-facing message that never contains server paths or stack traces.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code = "internal_error"
    status_code = 500
    default_message = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class ConfigurationError(AppError):
    code = "configuration_error"
    status_code = 500


class UploadRejected(AppError):
    code = "upload_rejected"
    status_code = 422
    default_message = "The uploaded file is not a supported Word (.docx) document."


class PayloadTooLarge(AppError):
    code = "payload_too_large"
    status_code = 413
    default_message = "The uploaded file exceeds the size limit."


class InputTooLong(AppError):
    code = "input_too_long"
    status_code = 422


class NotFound(AppError):
    code = "not_found"
    status_code = 404
    default_message = "The requested resource was not found."


class Unauthorized(AppError):
    code = "unauthorized"
    status_code = 401
    default_message = "Authentication is required."


class Conflict(AppError):
    code = "conflict"
    status_code = 409


class StaleReview(Conflict):
    code = "stale_review"
    default_message = "The review changed since it was loaded. Reload the proposals and try again."


class InvalidState(Conflict):
    code = "invalid_state"


class DeliveryBlocked(AppError):
    code = "delivery_blocked"
    status_code = 409
    default_message = "The edited document did not pass the delivery gates and cannot be downloaded."


class EditApplicationError(AppError):
    code = "edit_application_failed"
    status_code = 422


class LLMError(AppError):
    code = "llm_error"
    status_code = 502
    default_message = "The language model request failed."


class LLMUnavailable(LLMError):
    code = "llm_unavailable"
    status_code = 503
    default_message = "The language model service is unavailable."


class LLMOutputInvalid(LLMError):
    code = "llm_output_invalid"
    default_message = "The language model returned output that did not match the required schema."


class RendererUnavailable(AppError):
    code = "renderer_unavailable"
    status_code = 503
    default_message = "The document renderer is unavailable."
