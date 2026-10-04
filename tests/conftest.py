from __future__ import annotations

import shutil
from functools import cache

import pytest

from app.document.upload_validator import UploadLimits
from app.evaluation.corpus import FIXTURES, fixture_by_id

LIMITS = UploadLimits(
    max_upload_bytes=5 * 1024 * 1024, max_uncompressed_bytes=50 * 1024 * 1024, max_zip_members=500, max_compression_ratio=100.0
)

HAS_RENDERER = shutil.which("soffice") is not None and shutil.which("pdftoppm") is not None

try:  # optional heavy dependencies
    import fastapi  # noqa: F401

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

try:
    import langgraph  # noqa: F401

    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False


@cache
def fixture_bytes(fixture_id: str) -> bytes:
    return fixture_by_id(fixture_id).build()


@pytest.fixture
def limits() -> UploadLimits:
    return LIMITS


@pytest.fixture(params=[fx.fixture_id for fx in FIXTURES])
def any_fixture(request) -> tuple[str, bytes]:
    return request.param, fixture_bytes(request.param)


def pytest_collection_modifyitems(config, items):
    skip_renderer = pytest.mark.skip(reason="LibreOffice/pdftoppm not installed")
    skip_fastapi = pytest.mark.skip(reason="FastAPI not installed in this environment")
    skip_langgraph = pytest.mark.skip(reason="LangGraph not installed in this environment")
    for item in items:
        if "renderer" in item.keywords and not HAS_RENDERER:
            item.add_marker(skip_renderer)
        if "requires_fastapi" in item.keywords and not HAS_FASTAPI:
            item.add_marker(skip_fastapi)
        if "requires_langgraph" in item.keywords and not HAS_LANGGRAPH:
            item.add_marker(skip_langgraph)
