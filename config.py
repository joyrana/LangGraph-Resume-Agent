#!/usr/bin/env python3
"""Application settings for the resume optimization platform."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = Field(default="gpt-oss:latest")
    debug: bool = False
    frontend_origin: str = "http://localhost:5173"
    uploads_dir: Path = Path("storage/uploads")
    generated_dir: Path = Path("storage/generated")

    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "populate_by_name": True,
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
