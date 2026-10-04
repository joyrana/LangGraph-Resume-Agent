"""Helpers to build malformed or malicious DOCX variants for tests."""

from __future__ import annotations

import io
import zipfile


def rebuild(
    data: bytes, replace: dict[str, bytes] | None = None, add: dict[str, bytes] | None = None, remove: set[str] | None = None
) -> bytes:
    replace = replace or {}
    add = add or {}
    remove = remove or set()
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename in remove:
                continue
            dst.writestr(info.filename, replace.get(info.filename, src.read(info)))
        for name, payload in add.items():
            dst.writestr(name, payload)
    return out.getvalue()


def read_member(data: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(name)
