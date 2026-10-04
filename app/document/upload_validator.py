"""Validation of untrusted DOCX uploads.

Checks, in order: extension, declared MIME type (secondary), size, file
signature, ZIP structure and member names, streamed decompression limits,
required OOXML parts, main-part content type (rejects macro-enabled and
templates), XML safety of every XML part, relationship targets, and
finally that python-docx can open the package.

Nothing in the package is executed: macros, embedded objects and external
content are only detected and reported.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.core.errors import PayloadTooLarge, UploadRejected
from app.document.ooxml import (
    MACRO_DOCUMENT_CT,
    MAIN_DOCUMENT_CT,
    REL_OFFICE_DOCUMENT,
    REL_TYPE_PREFIX,
    TEMPLATE_DOCUMENT_CTS,
    UnsafeXMLError,
    content_types,
    parse_relationships,
    parse_xml,
    rels_path_for,
    resolve_target,
)

ALLOWED_MIME_TYPES = {
    "",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
}
OLE_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
_CHUNK = 64 * 1024
_RATIO_CHECK_MIN_BYTES = 1024 * 1024


@dataclass(frozen=True)
class UploadLimits:
    max_upload_bytes: int
    max_uncompressed_bytes: int
    max_zip_members: int
    max_compression_ratio: float


@dataclass
class UploadValidationResult:
    safe_file_name: str
    size_bytes: int
    uncompressed_bytes: int
    main_part: str
    warnings: list[str] = field(default_factory=list)
    external_hyperlinks: int = 0


def sanitize_file_name(name: str | None) -> str:
    base = PurePosixPath((name or "").replace("\\", "/")).name
    base = "".join(ch for ch in unicodedata.normalize("NFC", base) if unicodedata.category(ch)[0] != "C")
    base = re.sub(r"\s+", " ", base).strip()
    return base[:120] or "resume.docx"


def _reject(message: str, reason: str) -> UploadRejected:
    return UploadRejected(message, details={"reason": reason})


def _check_member_name(name: str) -> None:
    if not name or "\x00" in name or "\\" in name:
        raise _reject("The document package contains an invalid file name.", "invalid_member_name")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise _reject("The document package contains an absolute path.", "path_traversal")
    if any(part == ".." for part in name.split("/")):
        raise _reject("The document package contains a path traversal entry.", "path_traversal")


def validate_docx_upload(
    data: bytes,
    file_name: str | None,
    content_type: str | None,
    limits: UploadLimits,
) -> UploadValidationResult:
    safe_name = sanitize_file_name(file_name)

    # 1. Extension
    if not safe_name.lower().endswith(".docx"):
        suffix = PurePosixPath(safe_name).suffix.lower() or "none"
        raise _reject(
            f"Only Word .docx files are supported (received '{suffix}'). Save the resume as .docx in Word and upload it again.",
            "unsupported_extension",
        )

    # 2. MIME type (secondary; browsers are inconsistent)
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise _reject(
            f"The file was sent as '{mime}', which is not a Word .docx document.",
            "unsupported_mime_type",
        )

    # 3. Size
    if len(data) > limits.max_upload_bytes:
        raise PayloadTooLarge(
            f"The file is {len(data) // 1024} KB; the limit is {limits.max_upload_bytes // 1024} KB.",
            details={"reason": "too_large"},
        )
    if len(data) == 0:
        raise _reject("The uploaded file is empty.", "empty_file")

    # 4. Signature
    if data.startswith(OLE_CFB_MAGIC):
        raise _reject(
            "This file is password-protected or a legacy .doc file. Remove the password or re-save it as .docx.",
            "encrypted_or_legacy",
        )
    if not data.startswith(ZIP_MAGIC):
        raise _reject("The file is not a valid .docx package (it is not a ZIP archive).", "not_zip")

    # 5. ZIP structure
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError) as exc:
        raise _reject("The .docx package is corrupt and cannot be opened.", "bad_zip") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_zip_members:
            raise _reject("The document package contains too many parts.", "too_many_members")
        names: set[str] = set()
        declared_total = 0
        for info in infos:
            _check_member_name(info.filename)
            if info.filename in names:
                raise _reject("The document package contains duplicate parts.", "duplicate_member")
            names.add(info.filename)
            if info.flag_bits & 0x1:
                raise _reject("The document package is encrypted.", "encrypted_member")
            declared_total += info.file_size
            if declared_total > limits.max_uncompressed_bytes:
                raise _reject("The document expands beyond the allowed size.", "decompression_limit")
            if info.file_size >= _RATIO_CHECK_MIN_BYTES and info.compress_size > 0:
                if info.file_size / info.compress_size > limits.max_compression_ratio:
                    raise _reject("The document package has a suspicious compression ratio.", "compression_ratio")

        # 6. Stream every member to verify real (not declared) sizes.
        actual_total = 0
        xml_members: dict[str, bytes] = {}
        for info in infos:
            if info.is_dir():
                continue
            buf = io.BytesIO() if _is_xml_part(info.filename) else None
            try:
                with zf.open(info) as handle:
                    read = 0
                    while chunk := handle.read(_CHUNK):
                        read += len(chunk)
                        actual_total += len(chunk)
                        if read > info.file_size or actual_total > limits.max_uncompressed_bytes:
                            raise _reject("The document expands beyond the allowed size.", "decompression_limit")
                        if buf is not None:
                            buf.write(chunk)
            except UploadRejected:
                raise
            except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, NotImplementedError) as exc:
                raise _reject("The .docx package is corrupt and cannot be read.", "bad_member") from exc
            if buf is not None:
                xml_members[info.filename] = buf.getvalue()

    # 7. Required parts, content types, macros
    if "[Content_Types].xml" not in xml_members or "_rels/.rels" not in xml_members:
        raise _reject("The file is missing required Word package parts.", "missing_required_parts")
    if any(PurePosixPath(n).name.lower() == "vbaproject.bin" for n in names):
        raise _reject("Macro-enabled documents are not accepted. Save the resume as a plain .docx.", "macros")

    try:
        ct_root = parse_xml(xml_members["[Content_Types].xml"])
        root_rels = parse_relationships(xml_members["_rels/.rels"])
    except UnsafeXMLError as exc:
        raise _reject("The document package metadata is malformed.", "bad_xml") from exc

    main_targets = [r["target"] for r in root_rels if r["type"] == REL_OFFICE_DOCUMENT and r["mode"] != "External"]
    if len(main_targets) != 1:
        raise _reject("The file does not contain a Word document body.", "missing_main_part")
    main_part = resolve_target("", main_targets[0])
    if main_part not in names:
        raise _reject("The file does not contain a Word document body.", "missing_main_part")

    overrides, defaults = content_types(ct_root)
    main_ct = overrides.get(main_part) or defaults.get(PurePosixPath(main_part).suffix.lstrip(".").lower(), "")
    if main_ct == MACRO_DOCUMENT_CT:
        raise _reject("Macro-enabled documents (.docm) are not accepted.", "macros")
    if main_ct in TEMPLATE_DOCUMENT_CTS:
        raise _reject("Word templates (.dotx) are not accepted. Save the resume as a .docx document.", "template")
    if main_ct != MAIN_DOCUMENT_CT:
        raise _reject("The file is not a Word document.", "wrong_content_type")

    # 8. XML safety of every XML part
    for payload in xml_members.values():
        try:
            parse_xml(payload)
        except UnsafeXMLError as exc:
            raise _reject("The document contains malformed or unsafe XML.", "bad_xml") from exc

    # 9. Relationships
    warnings: list[str] = []
    external_hyperlinks = 0
    for rels_name, payload in xml_members.items():
        if not rels_name.endswith(".rels"):
            continue
        for rel in parse_relationships(payload):
            rel_type = rel["type"].removeprefix(REL_TYPE_PREFIX)
            if rel["mode"] == "External":
                if rel_type == "hyperlink":
                    external_hyperlinks += 1
                    continue
                # Any other external target (remote template, linked image, OLE package, frame,
                # sub-document) would be fetched by Word or by the renderer. Reject it.
                raise _reject(
                    "The document links to external content (for example a remote template or linked image), "
                    "which is not accepted. Embed the content in the document and upload it again.",
                    "external_content",
                )
            elif rel["target"] and not rel["target"].startswith("#"):
                if _escapes_package(_rels_source(rels_name), rel["target"]):
                    raise _reject("The document package contains an invalid internal reference.", "path_traversal")

    if any(n.startswith("word/embeddings/") for n in names):
        warnings.append("embedded_objects_present")

    # 10. python-docx must be able to open it.
    try:
        from docx import Document

        Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises a variety of types for broken packages
        raise _reject("Word document structure could not be read.", "unreadable") from exc

    if rels_path_for(main_part) not in xml_members:
        warnings.append("main_part_has_no_relationships")

    return UploadValidationResult(
        safe_file_name=safe_name,
        size_bytes=len(data),
        uncompressed_bytes=actual_total,
        main_part=main_part,
        warnings=sorted(set(warnings)),
        external_hyperlinks=external_hyperlinks,
    )


def _is_xml_part(name: str) -> bool:
    return name.endswith((".xml", ".rels"))


def _escapes_package(source_part: str, target: str) -> bool:
    if target.startswith("/"):
        return False
    depth = len([p for p in source_part.rpartition("/")[0].split("/") if p])
    for piece in target.split("/"):
        if piece == "..":
            depth -= 1
            if depth < 0:
                return True
        elif piece and piece != ".":
            depth += 1
    return False


def _rels_source(rels_name: str) -> str:
    folder, _, base = rels_name.rpartition("/")
    parent = folder.rpartition("/")[0] if folder.endswith("_rels") else folder
    source = base[: -len(".rels")]
    return f"{parent}/{source}" if parent else source
