"""Compatibility entry point: ``uvicorn main:app`` keeps working."""

from __future__ import annotations

from app.api.main import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    from app.core.config import get_settings

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=get_settings().debug, log_level="info")  # noqa: S104
