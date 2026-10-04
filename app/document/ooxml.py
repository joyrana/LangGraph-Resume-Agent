"""OOXML constants and a hardened XML parser.

All XML from uploaded packages is parsed with entity resolution, DTD loading
and network access disabled. Documents declaring a DOCTYPE are rejected
before parsing (DOCX parts never legitimately contain one).
"""

from __future__ import annotations

import re
import zipfile

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
V_NS = "urn:schemas-microsoft-com:vml"
XML_NS = "http://www.w3.org/XML/1998/namespace"

REL_OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
REL_TYPE_PREFIX = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"

MAIN_DOCUMENT_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
MACRO_DOCUMENT_CT = "application/vnd.ms-word.document.macroEnabled.main+xml"
TEMPLATE_DOCUMENT_CTS = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
}
HEADER_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"
FOOTER_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"


def w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


class UnsafeXMLError(ValueError):
    """Raised when XML is malformed or uses forbidden constructs."""


_DOCTYPE_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_blank_text=False,
        remove_comments=False,
        recover=False,
    )


def parse_xml(data: bytes) -> etree._Element:
    if _DOCTYPE_RE.search(data[:4096]) or _DOCTYPE_RE.search(data):
        raise UnsafeXMLError("XML DOCTYPE/ENTITY declarations are not allowed")
    try:
        return etree.fromstring(data, parser=_parser())
    except etree.XMLSyntaxError as exc:
        raise UnsafeXMLError(f"Malformed XML: {exc.msg}") from exc


def serialize_xml(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    return zf.read(name)


def content_types(ct_root: etree._Element) -> tuple[dict[str, str], dict[str, str]]:
    """Return (overrides by part name without leading slash, defaults by extension)."""
    overrides: dict[str, str] = {}
    defaults: dict[str, str] = {}
    for el in ct_root:
        name = local_name(el.tag)
        if name == "Override":
            overrides[el.get("PartName", "").lstrip("/")] = el.get("ContentType", "")
        elif name == "Default":
            defaults[el.get("Extension", "").lower()] = el.get("ContentType", "")
    return overrides, defaults


def rels_path_for(part_name: str) -> str:
    folder, _, base = part_name.rpartition("/")
    return f"{folder}/_rels/{base}.rels" if folder else f"_rels/{base}.rels"


def resolve_target(source_part: str, target: str) -> str:
    """Resolve a relationship target relative to its source part."""
    if target.startswith("/"):
        return target.lstrip("/")
    folder = source_part.rpartition("/")[0]
    parts = [p for p in folder.split("/") if p] if folder else []
    for piece in target.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def parse_relationships(data: bytes) -> list[dict[str, str]]:
    root = parse_xml(data)
    rels = []
    for el in root:
        if local_name(el.tag) != "Relationship":
            continue
        rels.append(
            {
                "id": el.get("Id", ""),
                "type": el.get("Type", ""),
                "target": el.get("Target", ""),
                "mode": el.get("TargetMode", "Internal"),
            }
        )
    return rels
