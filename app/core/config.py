"""Environment-based configuration, validated at startup.

Variable names keep the original ``OLLAMA_*``, ``DEBUG`` and ``FRONTEND_ORIGIN``
names for compatibility; new settings use the ``RESUME_`` prefix.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.core.errors import ConfigurationError

_ENV_MAP: dict[str, str] = {
    # field name -> environment variable
    "debug": "DEBUG",
    "frontend_origins": "FRONTEND_ORIGIN",
    "llm_provider": "RESUME_LLM_PROVIDER",
    "ollama_base_url": "OLLAMA_BASE_URL",
    "ollama_model": "OLLAMA_MODEL",
    "llm_temperature": "RESUME_LLM_TEMPERATURE",
    "llm_timeout_seconds": "RESUME_LLM_TIMEOUT_SECONDS",
    "llm_max_tokens": "RESUME_LLM_MAX_TOKENS",
    "llm_max_retries": "RESUME_LLM_MAX_RETRIES",
    "llm_context_char_budget": "RESUME_LLM_CONTEXT_CHAR_BUDGET",
    "llm_external_provider_acknowledged": "RESUME_LLM_EXTERNAL_PROVIDER_ACKNOWLEDGED",
    "storage_dir": "RESUME_STORAGE_DIR",
    "database_path": "RESUME_DATABASE_PATH",
    "max_upload_bytes": "RESUME_MAX_UPLOAD_BYTES",
    "max_uncompressed_bytes": "RESUME_MAX_UNCOMPRESSED_BYTES",
    "max_zip_members": "RESUME_MAX_ZIP_MEMBERS",
    "max_compression_ratio": "RESUME_MAX_COMPRESSION_RATIO",
    "max_job_description_chars": "RESUME_MAX_JOB_DESCRIPTION_CHARS",
    "max_company_details_chars": "RESUME_MAX_COMPANY_DETAILS_CHARS",
    "max_candidate_notes_chars": "RESUME_MAX_CANDIDATE_NOTES_CHARS",
    "max_proposals": "RESUME_MAX_PROPOSALS",
    "session_ttl_hours": "RESUME_SESSION_TTL_HOURS",
    "download_link_ttl_seconds": "RESUME_DOWNLOAD_LINK_TTL_SECONDS",
    "signing_secret": "RESUME_SIGNING_SECRET",
    "api_auth_token": "RESUME_API_AUTH_TOKEN",
    "deployment_mode": "RESUME_DEPLOYMENT_MODE",
    "renderer_enabled": "RESUME_RENDERER_ENABLED",
    "soffice_path": "RESUME_SOFFICE_PATH",
    "pdftoppm_path": "RESUME_PDFTOPPM_PATH",
    "renderer_expected_version": "RESUME_RENDERER_EXPECTED_VERSION",
    "renderer_timeout_seconds": "RESUME_RENDERER_TIMEOUT_SECONDS",
    "render_dpi": "RESUME_RENDER_DPI",
    "visual_gate_required": "RESUME_VISUAL_GATE_REQUIRED",
    "log_level": "RESUME_LOG_LEVEL",
    "log_content": "RESUME_LOG_CONTENT",
}

_SECRET_PLACEHOLDER = "change-me"  # noqa: S105 - sentinel, rejected in shared mode


class Settings(BaseModel):
    """Validated application settings."""

    model_config = {"frozen": True}

    debug: bool = False
    frontend_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

    # LLM
    llm_provider: Literal["ollama", "disabled"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss:latest"
    llm_temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    llm_timeout_seconds: float = Field(default=180.0, gt=0, le=1800)
    llm_max_tokens: int = Field(default=4096, ge=256, le=32768)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_context_char_budget: int = Field(default=24000, ge=2000, le=200000)
    llm_external_provider_acknowledged: bool = False

    # Storage
    storage_dir: Path = Path("storage")
    database_path: Path | None = None

    # Upload limits
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_uncompressed_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    max_zip_members: int = Field(default=500, ge=10, le=10000)
    max_compression_ratio: float = Field(default=100.0, ge=2.0)
    max_job_description_chars: int = Field(default=20000, ge=100)
    max_company_details_chars: int = Field(default=5000, ge=0)
    max_candidate_notes_chars: int = Field(default=5000, ge=0)
    max_proposals: int = Field(default=25, ge=1, le=100)

    # Lifecycle and access
    session_ttl_hours: int = Field(default=24, ge=1, le=24 * 90)
    download_link_ttl_seconds: int = Field(default=300, ge=30, le=86400)
    signing_secret: str = _SECRET_PLACEHOLDER
    api_auth_token: str | None = None
    deployment_mode: Literal["local", "shared"] = "local"

    # Rendering / visual gate
    renderer_enabled: bool = True
    soffice_path: str = "soffice"
    pdftoppm_path: str = "pdftoppm"
    renderer_expected_version: str | None = None
    renderer_timeout_seconds: int = Field(default=120, ge=10, le=900)
    render_dpi: int = Field(default=100, ge=50, le=300)
    visual_gate_required: bool = False

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_content: bool = False

    @field_validator("frontend_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("ollama_base_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("OLLAMA_BASE_URL must start with http:// or https://")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _validate_security(self) -> Settings:
        if self.deployment_mode == "shared":
            if self.signing_secret == _SECRET_PLACEHOLDER or len(self.signing_secret) < 32:
                raise ValueError("RESUME_SIGNING_SECRET must be set to >= 32 random characters in shared mode")
            if not self.api_auth_token or len(self.api_auth_token) < 24:
                raise ValueError("RESUME_API_AUTH_TOKEN must be set (>= 24 chars) in shared mode")
        if self.max_uncompressed_bytes < self.max_upload_bytes:
            raise ValueError("RESUME_MAX_UNCOMPRESSED_BYTES must be >= RESUME_MAX_UPLOAD_BYTES")
        return self

    @property
    def resolved_database_path(self) -> Path:
        return self.database_path or (self.storage_dir / "resume_agent.sqlite3")

    @property
    def llm_is_remote(self) -> bool:
        host = self.ollama_base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        return host not in {"localhost", "127.0.0.1", "::1", "ollama", "host.docker.internal"}


def _read_dotenv(path: Path) -> dict[str, str]:
    """Minimal ``KEY=VALUE`` reader. Real environment variables take precedence."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_settings(environ: dict[str, str] | None = None, dotenv_path: Path | None = Path(".env")) -> Settings:
    """Build settings from an environment mapping (defaults to ``os.environ`` + ``.env``)."""
    source: dict[str, str] = {}
    if environ is None:
        if dotenv_path is not None:
            source.update(_read_dotenv(dotenv_path))
        source.update(os.environ)
    else:
        source.update(environ)

    raw: dict[str, object] = {}
    for field_name, env_name in _ENV_MAP.items():
        if env_name in source and source[env_name] != "":
            raw[field_name] = source[env_name]
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{_ENV_MAP.get(str(err['loc'][0]), err['loc'][0]) if err['loc'] else 'settings'}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigurationError(f"Invalid configuration: {problems}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
