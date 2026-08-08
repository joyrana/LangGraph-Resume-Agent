from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable

from docx import Document
from pypdf import PdfReader
from fastapi import UploadFile


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc"}


async def extract_resume_text(upload_file: UploadFile) -> str:
    file_bytes = await upload_file.read()
    suffix = Path(upload_file.filename or "").suffix.lower()

    if suffix == ".pdf":
        return _extract_pdf_text(file_bytes)
    if suffix in {".docx", ".doc"}:
        return _extract_docx_text(file_bytes)

    raise ValueError("Unsupported file type. Upload a PDF or Word document.")


def _extract_pdf_text(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(page.strip() for page in pages if page.strip())


def _extract_docx_text(file_bytes: bytes) -> str:
    document = Document(io.BytesIO(file_bytes))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    return "\n".join(paragraphs)


def create_resume_docx(resume_text: str, destination: Path) -> Path:
    document = Document()
    document.add_heading("ATS-Optimized Resume", level=0)

    current_section = None
    for line in resume_text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned.endswith(":") and len(cleaned) < 60:
            current_section = cleaned[:-1]
            document.add_heading(current_section, level=1)
            continue
        document.add_paragraph(cleaned)

    destination.parent.mkdir(parents=True, exist_ok=True)
    document.save(destination)
    return destination
