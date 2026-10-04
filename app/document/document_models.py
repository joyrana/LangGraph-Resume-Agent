"""Public models describing a parsed DOCX and its editable locations."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Container(str, Enum):
    BODY = "body"
    TABLE = "table"
    HEADER = "header"
    FOOTER = "footer"
    TEXT_BOX = "text_box"
    CONTENT_CONTROL = "content_control"


class TableCellRef(BaseModel):
    table_path: str
    row: int
    column: int
    nesting_depth: int


class Block(BaseModel):
    """One paragraph-level location in the source document."""

    location_id: str
    part: str
    path: str
    container: Container
    section: str | None = None
    style_id: str | None = None
    style_name: str | None = None
    heading_level: int | None = None
    is_list_item: bool = False
    list_level: int | None = None
    text: str
    text_hash: str
    editable: bool
    read_only_reason: str | None = None
    run_count: int = 0
    distinct_run_formats: int = 0
    has_hyperlink: bool = False
    has_field: bool = False
    table_cell: TableCellRef | None = None


class SectionInfo(BaseModel):
    index: int
    page_width: int | None = None
    page_height: int | None = None
    orientation: str = "portrait"
    margins: dict[str, int] = Field(default_factory=dict)
    columns: int = 1
    section_type: str | None = None
    header_refs: int = 0
    footer_refs: int = 0


class FeatureInventory(BaseModel):
    paragraphs: int = 0
    tables: int = 0
    nested_tables: int = 0
    images: int = 0
    hyperlinks: int = 0
    text_boxes: int = 0
    fields: int = 0
    tracked_changes: int = 0
    comments: int = 0
    footnotes: bool = False
    endnotes: bool = False
    content_controls: int = 0
    math: int = 0
    embedded_objects: int = 0
    charts: int = 0
    smart_art: int = 0
    sections: int = 0
    multi_column_sections: int = 0
    headers: int = 0
    footers: int = 0
    alternate_content: int = 0


class DocumentIndex(BaseModel):
    document_version: str
    main_part: str
    blocks: list[Block]
    sections: list[SectionInfo]
    features: FeatureInventory
    limitations: list[str] = Field(default_factory=list)

    def block(self, location_id: str) -> Block | None:
        for blk in self.blocks:
            if blk.location_id == location_id:
                return blk
        return None

    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text)
