"""Headless LibreOffice rendering (DOCX → PDF → PNG) in an isolated profile.

Each call runs a single ``soffice`` process with its own throwaway user
profile and HOME, a hard timeout, and no reliance on a running office
instance. Uploads containing external relationships are rejected earlier,
so conversion does not need network access.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.errors import RendererUnavailable


@dataclass(frozen=True)
class RenderedDocument:
    name: str
    pdf_path: Path
    page_images: list[Path]


@lru_cache(maxsize=4)
def libreoffice_version(soffice_path: str) -> str | None:
    exe = shutil.which(soffice_path)
    if not exe:
        return None
    try:
        with tempfile.TemporaryDirectory() as home:
            out = subprocess.run(
                [exe, f"-env:UserInstallation=file://{home}/profile", "--version"],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "HOME": home},
            ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"LibreOffice\s+([\d.]+)", out)
    return match.group(1) if match else (out.strip() or None)


class LibreOfficeRenderer:
    def __init__(self, soffice_path: str = "soffice", pdftoppm_path: str = "pdftoppm", timeout_seconds: int = 120, dpi: int = 100) -> None:
        self.soffice_path = soffice_path
        self.pdftoppm_path = pdftoppm_path
        self.timeout_seconds = timeout_seconds
        self.dpi = dpi

    def available(self) -> bool:
        return shutil.which(self.soffice_path) is not None and shutil.which(self.pdftoppm_path) is not None

    def version(self) -> str | None:
        return libreoffice_version(self.soffice_path)

    def render(self, documents: dict[str, bytes], workdir: Path) -> dict[str, RenderedDocument]:
        """Render several DOCX payloads in one LibreOffice process."""
        if not self.available():
            raise RendererUnavailable("LibreOffice or pdftoppm is not installed.")
        for name in documents:
            if not re.fullmatch(r"[a-z0-9_]{1,40}", name):
                raise ValueError("Invalid render name")
        src_dir = workdir / "src"
        pdf_dir = workdir / "pdf"
        img_dir = workdir / "png"
        home = workdir / "home"
        for d in (src_dir, pdf_dir, img_dir, home):
            d.mkdir(parents=True, exist_ok=True)
        inputs = []
        for name, data in documents.items():
            path = src_dir / f"{name}.docx"
            path.write_bytes(data)
            inputs.append(str(path))

        cmd = [
            shutil.which(self.soffice_path) or self.soffice_path,
            f"-env:UserInstallation=file://{home}/profile",
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--nologo",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(pdf_dir),
            *inputs,
        ]
        env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "SAL_USE_VCLPLUGIN": "svp"}
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, start_new_session=True)
        try:
            proc.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise RendererUnavailable("Document rendering timed out.") from None

        results: dict[str, RenderedDocument] = {}
        for name in documents:
            pdf = pdf_dir / f"{name}.pdf"
            if not pdf.is_file() or pdf.stat().st_size == 0:
                continue  # caller decides whether a missing render is fatal
            prefix = img_dir / name
            try:
                subprocess.run(
                    [shutil.which(self.pdftoppm_path) or self.pdftoppm_path, "-r", str(self.dpi), "-png", str(pdf), str(prefix)],
                    check=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
            pages = sorted(img_dir.glob(f"{name}-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
            results[name] = RenderedDocument(name=name, pdf_path=pdf, page_images=pages)
        return results
