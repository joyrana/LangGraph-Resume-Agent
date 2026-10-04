"""Versioned synthetic DOCX fixture corpus.

Each fixture is generated deterministically with python-docx plus targeted
OOXML injection, so the corpus is reproducible without committing binaries.
The manifest records, for every fixture, the Word features it exercises,
controlled edits with their expected delivery decision, text that must be
read-only, and known limitations.

Limitation: these documents are produced by python-docx, not Microsoft Word.
Real Word-authored templates (heavy text-box layouts, theme fonts, complex
field nesting) are not represented; see docs/FIDELITY_EVALUATION.md.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.text import WD_LINE_SPACING
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from lxml import etree

CORPUS_VERSION = "2026.10.1"

_FIXED_DATE = (2026, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class ControlledEdit:
    """An edit to apply during evaluation. ``locate`` is a unique substring of the target paragraph."""

    locate: str
    original_text: str
    new_text: str
    expected: str  # "PASS" or "REVIEW_REQUIRED"
    note: str = ""


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    description: str
    features: list[str]
    build: Callable[[], bytes]
    edits: list[ControlledEdit] = field(default_factory=list)
    read_only_texts: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def _save(doc) -> bytes:
    doc.core_properties.author = "Fixture"
    buf = io.BytesIO()
    doc.save(buf)
    return _normalize_zip(buf.getvalue())


def _normalize_zip(data: bytes) -> bytes:
    """Fix member timestamps so fixture bytes are stable across runs."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            payload = src.read(info)
            if info.filename == "docProps/core.xml":
                payload = _fixed_core(payload)
            new = zipfile.ZipInfo(info.filename, date_time=_FIXED_DATE)
            new.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(new, payload)
    return out.getvalue()


def _fixed_core(payload: bytes) -> bytes:
    root = etree.fromstring(payload)
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.endswith(("}created", "}modified")):
            el.text = "2026-01-01T00:00:00Z"
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _run(paragraph, text, bold=False, italic=False, underline=False, color=None, size=None, font=None):
    run = paragraph.add_run(text)
    run.bold = bold or None
    run.italic = italic or None
    run.underline = underline or None
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    if size:
        run.font.size = Pt(size)
    if font:
        run.font.name = font
    return run


def _add_hyperlink(paragraph, url: str, text: str, bold: bool = False):
    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    style = OxmlElement("w:color")
    style.set(qn("w:val"), "0563C1")
    rpr.append(style)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rpr.append(u)
    if bold:
        rpr.append(OxmlElement("w:b"))
    run.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    link.append(run)
    paragraph._p.append(link)
    return link


def _add_page_field(paragraph, instr: str = "PAGE"):
    def fld(kind):
        r = OxmlElement("w:r")
        c = OxmlElement("w:fldChar")
        c.set(qn("w:fldCharType"), kind)
        r.append(c)
        return r

    paragraph._p.append(fld("begin"))
    r = OxmlElement("w:r")
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = f" {instr} "
    r.append(it)
    paragraph._p.append(r)
    paragraph._p.append(fld("separate"))
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "1"
    r.append(t)
    paragraph._p.append(r)
    paragraph._p.append(fld("end"))


def _header_block(doc, name="Jordan Lee"):
    doc.add_paragraph(name, style="Title")
    contact = doc.add_paragraph()
    _run(contact, "jordan.lee@example.com | +1 555 0100 | Springfield")


def _png_bytes(color=(40, 90, 160)) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (240, 80), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([4, 4, 236, 76], outline=color, width=4)
    draw.ellipse([20, 15, 70, 65], fill=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- fixtures


def build_simple_one_page() -> bytes:
    doc = Document()
    _header_block(doc)
    doc.add_heading("Summary", level=1)
    doc.add_paragraph("Backend engineer with five years of experience building data-heavy web services in Python.")
    doc.add_heading("Experience", level=1)
    job = doc.add_paragraph()
    _run(job, "Software Engineer", bold=True)
    _run(job, ", Acme Corp — 2019 to 2023")
    doc.add_paragraph("Built REST APIs in Python and FastAPI serving 2M requests per day.", style="List Bullet")
    doc.add_paragraph("Worked on the migration of the billing system to PostgreSQL.", style="List Bullet")
    doc.add_paragraph("Responsible for code reviews and mentoring two junior engineers.", style="List Bullet")
    doc.add_heading("Skills", level=1)
    doc.add_paragraph("Python, FastAPI, PostgreSQL, Docker, AWS")
    doc.add_heading("Education", level=1)
    doc.add_paragraph("B.Sc. Computer Science, State University, 2018")
    return _save(doc)


def build_rich_runs() -> bytes:
    doc = Document()
    _header_block(doc)
    doc.add_heading("Experience", level=1)
    p = doc.add_paragraph()
    _run(p, "Designed ")
    _run(p, "event-driven", bold=True)
    _run(p, " data pipelines ", italic=True)
    _run(p, "using Kafka", underline=True, color="C00000")
    _run(p, " for the analytics team.")
    p2 = doc.add_paragraph()
    _run(p2, "Led proj")
    _run(p2, "ect manage", italic=True)
    _run(p2, "ment for a 6-person team across two time zones.")
    p3 = doc.add_paragraph()
    _run(p3, "Skills: ", bold=True)
    _run(p3, "Python", color="1F4E79")
    _run(p3, ", ")
    _run(p3, "SQL", color="1F4E79")
    _run(p3, ", and ")
    _run(p3, "Airflow", color="1F4E79")
    return _save(doc)


def build_nested_numbering() -> bytes:
    doc = Document()
    _header_block(doc)
    doc.add_heading("Projects", level=1)
    doc.add_paragraph("Inventory forecasting service", style="List Number")
    doc.add_paragraph("Reduced manual spreadsheet work for the planning team.", style="List Bullet 2")
    doc.add_paragraph("Used Python and scikit-learn for weekly demand models.", style="List Bullet 2")
    doc.add_paragraph("Internal developer portal", style="List Number")
    doc.add_paragraph("Helped onboard new hires with a searchable service catalog.", style="List Bullet 2")
    doc.add_paragraph("Third-level detail retained for numbering checks.", style="List Bullet 3")
    return _save(doc)


def build_tables() -> bytes:
    doc = Document()
    _header_block(doc)
    table = doc.add_table(rows=3, cols=2)
    table.style = "Table Grid"
    top = table.cell(0, 0).merge(table.cell(0, 1))
    top.paragraphs[0].add_run("Professional Profile").bold = True
    table.cell(1, 0).paragraphs[0].add_run("Skills").bold = True
    table.cell(1, 0).add_paragraph("Python, Go, Terraform")
    table.cell(1, 1).paragraphs[0].add_run("Experience").bold = True
    table.cell(1, 1).add_paragraph("Maintained CI pipelines for twelve services.")
    table.cell(1, 1).add_paragraph("Wrote runbooks for the on-call rotation.")
    cell = table.cell(2, 1)
    cell.paragraphs[0].add_run("Certifications")
    nested = cell.add_table(rows=2, cols=2)
    nested.style = "Table Grid"
    nested.cell(0, 0).text = "AWS Solutions Architect Associate"
    nested.cell(0, 1).text = "2022"
    nested.cell(1, 0).text = "Certified Kubernetes Administrator"
    nested.cell(1, 1).text = "2023"
    table.cell(2, 0).paragraphs[0].add_run("Languages")
    doc.add_paragraph("References available on request.")
    return _save(doc)


def build_columns_sections() -> bytes:
    doc = Document()
    _header_block(doc)
    doc.add_heading("Summary", level=1)
    doc.add_paragraph("Data analyst focused on clear reporting and reliable dashboards for operations teams.")
    sec = doc.add_section(WD_SECTION.CONTINUOUS)
    cols = sec._sectPr.find(qn("w:cols"))
    if cols is None:
        cols = OxmlElement("w:cols")
        sec._sectPr.append(cols)
    cols.set(qn("w:num"), "2")
    cols.set(qn("w:space"), "720")
    doc.add_heading("Skills", level=1)
    for skill in [
        "SQL and window functions",
        "Power BI dashboards",
        "Python with pandas",
        "Excel modelling",
        "Stakeholder interviews",
        "Data quality checks",
    ]:
        doc.add_paragraph(skill, style="List Bullet")
    sec3 = doc.add_section(WD_SECTION.NEW_PAGE)
    sec3.orientation = WD_ORIENT.LANDSCAPE
    sec3.page_width, sec3.page_height = sec3.page_height, sec3.page_width
    cols3 = sec3._sectPr.find(qn("w:cols"))
    if cols3 is not None:
        cols3.set(qn("w:num"), "1")
    doc.add_heading("Selected Reports", level=1)
    doc.add_paragraph("Monthly operations review covering fulfilment times and backlog trends.")
    return _save(doc)


def build_header_footer_links() -> bytes:
    doc = Document()
    section = doc.sections[0]
    hp = section.header.paragraphs[0]
    _run(hp, "Jordan Lee — ", bold=True)
    _add_hyperlink(hp, "https://portfolio.example.com", "portfolio.example.com")
    fp = section.footer.paragraphs[0]
    _run(fp, "Page ")
    _add_page_field(fp, "PAGE")
    doc.add_heading("Summary", level=1)
    p = doc.add_paragraph()
    _run(p, "Frontend engineer; selected work at ")
    _add_hyperlink(p, "https://github.com/example", "github.com/example")
    _run(p, " and in the projects below.")
    doc.add_heading("Experience", level=1)
    doc.add_paragraph("Built accessible React components used by four product teams.", style="List Bullet")
    doc.add_paragraph("Improved page load time by moving images to a CDN.", style="List Bullet")
    return _save(doc)


def build_images() -> bytes:
    doc = Document()
    doc.add_picture(io.BytesIO(_png_bytes()), width=Pt(120))
    _header_block(doc)
    doc.add_heading("Experience", level=1)
    doc.add_paragraph("Designed marketing assets and maintained the brand style guide.")
    p = doc.add_paragraph()
    _run(p, "Portfolio sample: ")
    p.add_run().add_picture(io.BytesIO(_png_bytes((160, 60, 40))), width=Pt(60))
    _run(p, " shown at actual size.")
    doc.add_paragraph("Coordinated print production with three external vendors.")
    return _save(doc)


def build_multipage() -> bytes:
    doc = Document()
    _header_block(doc)
    doc.add_heading("Experience", level=1)
    companies = ["Northwind", "Contoso", "Fabrikam", "Tailspin", "Wingtip", "Litware"]
    for i, company in enumerate(companies):
        job = doc.add_paragraph()
        _run(job, f"Engineer {i + 1}", bold=True)
        _run(job, f", {company} — {2012 + i} to {2013 + i}")
        for j in range(6):
            doc.add_paragraph(
                f"Delivered feature set {j + 1} for the {company} platform, coordinating with design and QA "
                f"to ship on a two-week cadence and documenting decisions for future maintainers.",
                style="List Bullet",
            )
    doc.add_heading("Education", level=1)
    doc.add_paragraph("M.Sc. Software Engineering, Northern Institute, 2011")
    return _save(doc)


def build_fonts_spacing() -> bytes:
    doc = Document()
    title = doc.add_paragraph()
    _run(title, "Jordan Lee", bold=True, size=20, font="Garamond")
    p = doc.add_paragraph()
    _run(p, "Systems administrator with a focus on automation.", size=9, font="Courier New")
    fmt = p.paragraph_format
    fmt.space_before = Pt(18)
    fmt.space_after = Pt(4)
    fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    fmt.line_spacing = Pt(11)
    q = doc.add_paragraph()
    r = _run(q, "Maintained Linux servers and patched them monthly.", size=14, font="Georgia")
    r.font.small_caps = True
    rpr = r._r.get_or_add_rPr()
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:val"), "20")
    rpr.append(spacing)
    doc.add_paragraph("Scripted backups with Bash and verified restores quarterly.")
    return _save(doc)


_VML_TEXTBOX = (
    '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:v="urn:schemas-microsoft-com:vml"><w:pict>'
    '<v:shape id="tb1" type="#_x0000_t202" style="width:200pt;height:40pt">'
    "<v:textbox><w:txbxContent><w:p><w:r><w:t>Text box: open to relocation</w:t></w:r></w:p>"
    "</w:txbxContent></v:textbox></v:shape></w:pict></w:r>"
)


def build_complex_elements() -> bytes:
    doc = Document()
    _header_block(doc)
    doc.add_heading("Summary", level=1)
    p = doc.add_paragraph()
    _run(p, "Operations lead. ")
    ins = etree.fromstring(
        '<w:ins xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:id="1" w:author="Editor" '
        'w:date="2026-01-01T00:00:00Z"><w:r><w:t>Inserted by tracked change.</w:t></w:r></w:ins>'
    )
    p._p.append(ins)
    dele = etree.fromstring(
        '<w:del xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:id="2" w:author="Editor" '
        'w:date="2026-01-01T00:00:00Z"><w:r><w:delText> Deleted text.</w:delText></w:r></w:del>'
    )
    p._p.append(dele)
    d = doc.add_paragraph()
    _run(d, "Updated: ")
    _add_page_field(d, "DATE \\@ yyyy")
    s = doc.add_paragraph()
    _run(s, "Availability: ")
    sdt = etree.fromstring(
        '<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:sdtPr><w:alias w:val="avail"/></w:sdtPr>'
        "<w:sdtContent><w:r><w:t>Immediately</w:t></w:r></w:sdtContent></w:sdt>"
    )
    s._p.append(sdt)
    _run(s, " after notice period.")
    b = doc.add_paragraph()
    start = etree.fromstring(
        '<w:bookmarkStart xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:id="5" w:name="exp"/>'
    )
    end = etree.fromstring('<w:bookmarkEnd xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:id="5"/>')
    _run(b, "Ran the warehouse ")
    b._p.append(start)
    _run(b, "night shift")
    b._p.append(end)
    _run(b, " for two years.")
    t = doc.add_paragraph()
    _run(t, "See note: ")
    t._p.append(etree.fromstring(_VML_TEXTBOX))
    doc.add_paragraph("Managed a team of eight associates.")
    return _save(doc)


FIXTURES: list[Fixture] = [
    Fixture(
        "simple_one_page",
        "One-page resume with headings, bullets and a mixed-format job line.",
        ["headings", "bullets", "run_formatting", "one_page"],
        build_simple_one_page,
        edits=[
            ControlledEdit(
                "Worked on the migration", "Worked on the migration of", "Migrated", "PASS", "short replacement, same line count"
            ),
            ControlledEdit(
                "Responsible for code reviews",
                "Responsible for code reviews and mentoring",
                "Led code reviews and mentored",
                "PASS",
                "verb change",
            ),
        ],
    ),
    Fixture(
        "rich_runs",
        "Paragraphs with bold/italic/colored runs and words split across runs.",
        ["run_formatting", "multi_run_text", "colors", "underline"],
        build_rich_runs,
        edits=[
            ControlledEdit(
                "Designed event-driven",
                "data pipelines using Kafka for the analytics team",
                "data pipelines using Kafka for analytics reporting",
                "PASS",
                "spans italic, underlined-red and plain runs",
            ),
            ControlledEdit(
                "Led project management",
                "Led project management for a 6-person team",
                "Managed projects for a 6-person team",
                "PASS",
                "range starts inside a word split across runs",
            ),
        ],
    ),
    Fixture(
        "nested_numbering",
        "Numbered list with nested bullet levels.",
        ["numbering", "nested_lists"],
        build_nested_numbering,
        edits=[ControlledEdit("Reduced manual spreadsheet", "Reduced manual spreadsheet work", "Cut manual spreadsheet work", "PASS")],
    ),
    Fixture(
        "tables",
        "Two-column table layout with merged header cell and a nested table.",
        ["tables", "merged_cells", "nested_tables"],
        build_tables,
        edits=[
            ControlledEdit(
                "Maintained CI pipelines",
                "Maintained CI pipelines",
                "Maintained and improved CI pipelines",
                "PASS",
                "text inside a table cell",
            ),
            ControlledEdit(
                "Certified Kubernetes",
                "Certified Kubernetes Administrator",
                "Certified Kubernetes Administrator (CKA)",
                "PASS",
                "nested table cell",
            ),
        ],
    ),
    Fixture(
        "columns_sections",
        "Continuous section break into two columns, then a landscape section.",
        ["sections", "columns", "landscape", "page_breaks"],
        build_columns_sections,
        edits=[
            ControlledEdit("Power BI", "Power BI dashboards", "Power BI dashboards and reports", "PASS", "edit inside a two-column section")
        ],
    ),
    Fixture(
        "header_footer_links",
        "Header with hyperlink, footer with PAGE field, body hyperlink.",
        ["headers", "footers", "fields", "hyperlinks"],
        build_header_footer_links,
        edits=[
            ControlledEdit(
                "Built accessible React", "Built accessible React components", "Built accessible, reusable React components", "PASS"
            ),
            ControlledEdit(
                "Frontend engineer; selected",
                "Frontend engineer; selected work",
                "Frontend engineer. Selected work",
                "PASS",
                "edit adjacent to a hyperlink",
            ),
        ],
        read_only_texts=["1"],
        limitations=["The PAGE field result in the footer is read-only."],
    ),
    Fixture(
        "images",
        "Block image and an inline image between text runs.",
        ["images", "inline_images"],
        build_images,
        edits=[ControlledEdit("Coordinated print", "with three external vendors", "with three external print vendors", "PASS")],
        limitations=["Edits may not span the inline image."],
    ),
    Fixture(
        "multipage",
        "Three-page resume with many bullets (page-flow sensitive).",
        ["multi_page", "bullets"],
        build_multipage,
        edits=[
            ControlledEdit(
                "Delivered feature set 1 for the Northwind",
                "coordinating with design and QA",
                "coordinating closely with the design and QA teams each sprint",
                "REVIEW_REQUIRED",
                "policy-compliant (+30 chars) replacement adds a line and shifts page breaks",
            ),
            ControlledEdit("Delivered feature set 3 for the Contoso", "Delivered", "Shipped", "PASS", "short replacement"),
        ],
    ),
    Fixture(
        "fonts_spacing",
        "Uncommon fonts, exact line spacing, small caps and character spacing.",
        ["fonts", "spacing", "small_caps", "character_spacing"],
        build_fonts_spacing,
        edits=[ControlledEdit("Maintained Linux servers", "patched them monthly", "patched them every month", "PASS")],
        limitations=["Garamond/Georgia may be substituted by the renderer if not installed; the comparison is still like-for-like."],
    ),
    Fixture(
        "complex_elements",
        "Tracked changes, a DATE field, an inline content control, a bookmark and a VML text box.",
        ["tracked_changes", "fields", "content_controls", "bookmarks", "text_boxes"],
        build_complex_elements,
        edits=[ControlledEdit("Managed a team of eight", "Managed a team", "Supervised a team", "PASS")],
        read_only_texts=["Inserted by tracked change.", "Immediately", "Text box: open to relocation"],
        limitations=[
            "Paragraphs with tracked changes are read-only.",
            "Content-control and field results are read-only; edits may not cross the bookmark.",
            "VML text-box text is read-only.",
        ],
    ),
]


def fixture_by_id(fixture_id: str) -> Fixture:
    for fx in FIXTURES:
        if fx.fixture_id == fixture_id:
            return fx
    raise KeyError(fixture_id)


def manifest() -> dict:
    return {
        "corpus_version": CORPUS_VERSION,
        "fixtures": [
            {
                "id": fx.fixture_id,
                "description": fx.description,
                "features": fx.features,
                "edits": [e.__dict__ for e in fx.edits],
                "read_only_texts": fx.read_only_texts,
                "limitations": fx.limitations,
            }
            for fx in FIXTURES
        ],
    }
