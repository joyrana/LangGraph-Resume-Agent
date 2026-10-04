"""Service-level harness: real repository, artifact store, editor and evaluator; scripted model."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from app.core.config import load_settings
from app.document.docx_parser import parse_docx
from app.document.visual_renderer import LibreOfficeRenderer
from app.llm.provider import ScriptedProvider
from app.persistence.artifact_store import ArtifactStore
from app.persistence.repositories import Repository
from app.services.resume_service import ResumeService
from tests.support.llm_fakes import batch_json, proposal

JD = "Backend engineer: Python, FastAPI and PostgreSQL. Code reviews and mentoring are part of the role."


@dataclass
class Harness:
    service: ResumeService
    provider: ScriptedProvider
    tmp: Path


def make_service(tmp: Path, responses: list, *, renderer: bool = True, **overrides) -> Harness:
    env = {
        "RESUME_STORAGE_DIR": str(tmp / "storage"),
        "RESUME_SIGNING_SECRET": "test-secret-" + "x" * 32,
        "RESUME_LLM_PROVIDER": "ollama",
        **{k: str(v) for k, v in overrides.items()},
    }
    settings = load_settings(env)
    repo = Repository(settings.resolved_database_path)
    store = ArtifactStore(settings.storage_dir / "artifacts")
    provider = ScriptedProvider(responses)
    service = ResumeService(settings, repo, store, provider, LibreOfficeRenderer() if renderer else None)
    return Harness(service, provider, tmp)


def standard_batch(data: bytes) -> str:
    index, _ = parse_docx(data)
    return batch_json(
        proposal(index, "Worked on the migration", "Worked on the migration of", "Migrated"),
        proposal(index, "Responsible for code reviews", "Responsible for code reviews and mentoring", "Led code reviews and mentored"),
        proposal(
            index,
            "Built REST APIs",
            "Built REST APIs in Python and FastAPI",
            "Built Python and FastAPI REST APIs",
            edit_type="keyword_alignment",
        ),
        summary="Strong overlap on Python and FastAPI.",
    )


def create_and_analyze(h: Harness, data: bytes, jd: str = JD, notes: str = ""):
    created = h.service.create_session(
        data=data,
        file_name="Jordan Lee CV.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        job_description=jd,
        company_details="Acme builds billing software.",
        candidate_notes=notes,
    )
    asyncio.run(h.service.run_analysis(created.session_id))
    return created.session_id, created.session_token
