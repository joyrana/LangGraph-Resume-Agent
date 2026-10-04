from __future__ import annotations

import io
import warnings
import zipfile

import pytest

from app.core.errors import PayloadTooLarge, UploadRejected
from app.document.upload_validator import UploadLimits, sanitize_file_name, validate_docx_upload
from tests.conftest import LIMITS, fixture_bytes
from tests.support.docx_mutation import read_member, rebuild

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _reason(exc_info) -> str:
    return exc_info.value.details.get("reason")


@pytest.fixture
def valid() -> bytes:
    return fixture_bytes("simple_one_page")


def test_valid_docx_accepted(valid):
    result = validate_docx_upload(valid, "My Resume.docx", DOCX_MIME, LIMITS)
    assert result.main_part == "word/document.xml"
    assert result.safe_file_name == "My Resume.docx"


@pytest.mark.parametrize("name", ["resume.pdf", "resume.doc", "resume.docm", "resume.rtf", "resume.odt", "resume", "resume.docx.exe"])
def test_rejects_non_docx_extensions(valid, name):
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(valid, name, DOCX_MIME, LIMITS)
    assert _reason(exc) == "unsupported_extension"


@pytest.mark.parametrize("mime", ["application/pdf", "application/msword", "text/plain", "image/png"])
def test_rejects_spoofed_mime(valid, mime):
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(valid, "resume.docx", mime, LIMITS)
    assert _reason(exc) == "unsupported_mime_type"


@pytest.mark.parametrize("mime", ["", "application/octet-stream", "application/zip", DOCX_MIME + "; charset=binary"])
def test_accepts_generic_browser_mime(valid, mime):
    validate_docx_upload(valid, "resume.docx", mime, LIMITS)


def test_pdf_renamed_to_docx_rejected():
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(b"%PDF-1.7\n1 0 obj\n", "resume.docx", "", LIMITS)
    assert _reason(exc) == "not_zip"


def test_encrypted_or_legacy_ole_rejected():
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512, "resume.docx", "", LIMITS)
    assert _reason(exc) == "encrypted_or_legacy"


def test_empty_file_rejected():
    with pytest.raises(UploadRejected):
        validate_docx_upload(b"", "resume.docx", "", LIMITS)


def test_oversized_upload_rejected(valid):
    small = UploadLimits(max_upload_bytes=1024, max_uncompressed_bytes=10_000_000, max_zip_members=500, max_compression_ratio=100)
    with pytest.raises(PayloadTooLarge):
        validate_docx_upload(valid, "resume.docx", "", small)


def test_truncated_zip_rejected(valid):
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(valid[: len(valid) // 2], "resume.docx", "", LIMITS)
    assert _reason(exc) in {"bad_zip", "bad_member"}


def test_zip_without_ooxml_parts_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(buf.getvalue(), "resume.docx", "", LIMITS)
    assert _reason(exc) == "missing_required_parts"


def test_missing_main_document_rejected(valid):
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(rebuild(valid, remove={"word/document.xml"}), "resume.docx", "", LIMITS)
    assert _reason(exc) == "missing_main_part"


def test_decompression_limit_zip_bomb(valid):
    bomb = rebuild(valid, add={"word/media/bomb.bin": b"\0" * (3 * 1024 * 1024)})
    tight = UploadLimits(
        max_upload_bytes=5_000_000, max_uncompressed_bytes=2 * 1024 * 1024, max_zip_members=500, max_compression_ratio=10_000
    )
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(bomb, "resume.docx", "", tight)
    assert _reason(exc) == "decompression_limit"


def test_high_compression_ratio_rejected(valid):
    bomb = rebuild(valid, add={"word/media/bomb.bin": b"\0" * (4 * 1024 * 1024)})
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(bomb, "resume.docx", "", LIMITS)
    assert _reason(exc) == "compression_ratio"


def test_too_many_members_rejected(valid):
    many = rebuild(valid, add={f"word/media/x{i}.bin": b"x" for i in range(20)})
    tight = UploadLimits(max_upload_bytes=5_000_000, max_uncompressed_bytes=50_000_000, max_zip_members=15, max_compression_ratio=100)
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(many, "resume.docx", "", tight)
    assert _reason(exc) == "too_many_members"


@pytest.mark.parametrize("name", ["../evil.xml", "word/../../evil.xml", "/etc/passwd", "C:/windows/x.xml", "word\\evil.xml"])
def test_path_traversal_member_names_rejected(valid, name):
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(rebuild(valid, add={name: b"<x/>"}), "resume.docx", "", LIMITS)
    assert _reason(exc) in {"path_traversal", "invalid_member_name"}


def test_duplicate_members_rejected(valid):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dup = rebuild(valid, add={"word/document.xml": read_member(valid, "word/document.xml")})
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(dup, "resume.docx", "", LIMITS)
    assert _reason(exc) == "duplicate_member"


def test_encrypted_member_flag_rejected(valid):
    raw = bytearray(valid)
    # Set the "encrypted" general-purpose flag bit in every central directory entry.
    idx = 0
    while (idx := raw.find(b"PK\x01\x02", idx)) != -1:
        raw[idx + 8] |= 0x1
        idx += 4
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(bytes(raw), "resume.docx", "", LIMITS)
    assert _reason(exc) == "encrypted_member"


def test_macro_enabled_content_type_rejected(valid):
    ct = read_member(valid, "[Content_Types].xml").replace(
        b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        b"application/vnd.ms-word.document.macroEnabled.main+xml",
    )
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(rebuild(valid, replace={"[Content_Types].xml": ct}), "resume.docx", "", LIMITS)
    assert _reason(exc) == "macros"


def test_vba_project_rejected(valid):
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(rebuild(valid, add={"word/vbaProject.bin": b"\0" * 64}), "resume.docx", "", LIMITS)
    assert _reason(exc) == "macros"


def test_template_rejected(valid):
    ct = read_member(valid, "[Content_Types].xml").replace(b"wordprocessingml.document.main+xml", b"wordprocessingml.template.main+xml")
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(rebuild(valid, replace={"[Content_Types].xml": ct}), "resume.docx", "", LIMITS)
    assert _reason(exc) == "template"


def test_xxe_doctype_rejected(valid):
    doc = read_member(valid, "word/document.xml")
    evil = doc.replace(
        b"<w:document",
        b'<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><w:document',
        1,
    )
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(rebuild(valid, replace={"word/document.xml": evil}), "resume.docx", "", LIMITS)
    assert _reason(exc) == "bad_xml"


def test_billion_laughs_rejected(valid):
    evil = b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><r>&b;</r>'
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(rebuild(valid, add={"customXml/item99.xml": evil}), "resume.docx", "", LIMITS)
    assert _reason(exc) == "bad_xml"


def test_malformed_xml_rejected(valid):
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(rebuild(valid, replace={"word/styles.xml": b"<w:styles><unclosed>"}), "resume.docx", "", LIMITS)
    assert _reason(exc) == "bad_xml"


@pytest.mark.parametrize("rel_type", ["attachedTemplate", "image", "oleObject", "subDocument", "frame"])
def test_external_non_hyperlink_relationships_rejected(valid, rel_type):
    rels = read_member(valid, "word/_rels/document.xml.rels").replace(
        b"</Relationships>",
        f'<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{rel_type}" '
        f'Target="https://attacker.example/x" TargetMode="External"/></Relationships>'.encode(),
    )
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(rebuild(valid, replace={"word/_rels/document.xml.rels": rels}), "resume.docx", "", LIMITS)
    assert _reason(exc) == "external_content"


def test_external_hyperlinks_allowed_and_counted():
    data = fixture_bytes("header_footer_links")
    result = validate_docx_upload(data, "resume.docx", "", LIMITS)
    assert result.external_hyperlinks == 2


def test_internal_relationship_traversal_rejected(valid):
    rels = read_member(valid, "word/_rels/document.xml.rels").replace(
        b"</Relationships>",
        b'<Relationship Id="rIdY" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../../../etc/passwd"/></Relationships>',
    )
    with pytest.raises(UploadRejected) as exc:
        validate_docx_upload(rebuild(valid, replace={"word/_rels/document.xml.rels": rels}), "resume.docx", "", LIMITS)
    assert _reason(exc) == "path_traversal"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.docx", "passwd.docx"),
        ("C:\\Users\\me\\cv.docx", "cv.docx"),
        ("name\x00with\x07controls.docx", "namewithcontrols.docx"),
        ("", "resume.docx"),
        ("a" * 300 + ".docx", "a" * 120),
    ],
)
def test_file_name_sanitized(raw, expected):
    assert sanitize_file_name(raw) == expected


def test_all_corpus_fixtures_are_valid(any_fixture):
    _, data = any_fixture
    validate_docx_upload(data, "resume.docx", DOCX_MIME, LIMITS)
