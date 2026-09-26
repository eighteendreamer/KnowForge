from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.identity import StrictModel

ElementType = Literal["Heading", "NarrativeText", "Table", "ListItem", "Code"]


class Element(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: ElementType
    text: str = Field(min_length=1)
    level: int | None = Field(default=None, ge=1, le=6)
    page: int | None = Field(default=None, ge=1)
    char_count: int = 0

    @model_validator(mode="after")
    def set_counts(self):
        self.char_count = len(self.text)
        if self.type == "Heading" and self.level is None:
            raise ValueError("Heading requires level")
        return self


class ParsedContent(BaseModel):
    title: str
    elements: list[Element]
    total_pages: int | None
    recognition_pages: list[int] = Field(default_factory=list)


class DocumentAST(BaseModel):
    doc_id: str
    title: str
    source_type: Literal["pdf", "html"]
    source_path: str
    upload_time: datetime
    uploader: str
    elements: list[Element]
    stats: dict[str, int | None]


class ChunkData(BaseModel):
    chunk_id: str
    doc_id: str
    chunk_index: int
    section_path: list[str]
    text: str
    text_with_context: str
    page_start: int | None
    page_end: int | None
    char_count: int
    token_count: int
    element_types: list[str]
    metadata: dict = Field(default_factory=dict)


class DocumentPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    category_id: int | None = Field(default=None, gt=0)
    is_public: bool | None = None


class DocumentTagsInput(StrictModel):
    tag_ids: list[int] = Field(max_length=100)


class UploadComplete(StrictModel):
    upload_id: UUID
    filename: str = Field(min_length=1, max_length=500)
    content_type: str | None = Field(default=None, max_length=100)
    total_parts: int = Field(ge=1, le=200)
    category_id: int | None = Field(default=None, gt=0)
