from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedMixin, IdentityMixin, UpdatedMixin


class Category(IdentityMixin, UpdatedMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (CheckConstraint("parent_id IS NULL OR parent_id <> id", name="parent_not_self"),)
    name: Mapped[str] = mapped_column(String(100))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    path: Mapped[str] = mapped_column(String(500), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")


class Document(IdentityMixin, UpdatedMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("file_type IN ('pdf', 'html')", name="file_type"),
        CheckConstraint("file_size > 0", name="file_size"),
        CheckConstraint(
            "status IN ('pending','parsing','chunking','embedding','tagging','indexing','ready','failed','deleting')",
            name="status",
        ),
        Index("idx_documents_status_time", "status", "upload_time"),
    )
    doc_id: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    original_filename: Mapped[str] = mapped_column(String(500))
    file_type: Mapped[str] = mapped_column(String(10))
    file_size: Mapped[int] = mapped_column(BigInteger)
    source_path: Mapped[str] = mapped_column(Text)
    ast_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    upload_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    parse_error: Mapped[str | None] = mapped_column(Text)
    total_pages: Mapped[int | None] = mapped_column(Integer)
    total_chunks: Mapped[int] = mapped_column(Integer, server_default="0")
    auto_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    manual_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    is_public: Mapped[bool] = mapped_column(Boolean, server_default="true")


class Tag(IdentityMixin, UpdatedMixin, Base):
    __tablename__ = "tags"
    __table_args__ = (
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence"),
        CheckConstraint("review_status IN ('pending','approved','rejected')", name="review_status"),
    )
    name: Mapped[str] = mapped_column(String(100), unique=True)
    normalized_name: Mapped[str] = mapped_column(String(100), unique=True)
    color: Mapped[str] = mapped_column(String(20), server_default="#18A058")
    auto_generated: Mapped[bool] = mapped_column(Boolean, server_default="false")
    confidence: Mapped[float] = mapped_column(Float, server_default="1")
    review_status: Mapped[str] = mapped_column(String(20), server_default="pending", index=True)


class Chunk(IdentityMixin, UpdatedMixin, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index"),
        CheckConstraint("chunk_index >= 0", name="chunk_index"),
        CheckConstraint("page_start IS NULL OR page_start >= 1", name="page_start"),
        CheckConstraint(
            "page_end IS NULL OR (page_start IS NOT NULL AND page_end >= page_start)", name="page_end"
        ),
        CheckConstraint("difficulty IN ('初级','中级','高级')", name="difficulty"),
        Index(
            "idx_chunks_text_trgm", "text", postgresql_using="gin", postgresql_ops={"text": "gin_trgm_ops"}
        ),
    )
    chunk_id: Mapped[str] = mapped_column(String(100), unique=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    text: Mapped[str] = mapped_column(Text)
    text_with_context: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    char_count: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    element_types: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    difficulty: Mapped[str | None] = mapped_column(String(10))
    qdrant_point_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), unique=True)


class DocumentTag(CreatedMixin, Base):
    __tablename__ = "document_tags"
    __table_args__ = (
        CheckConstraint("source IN ('auto','manual','rule')", name="source"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence"),
    )
    doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    source: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float, server_default="1")


class ChunkTag(CreatedMixin, Base):
    __tablename__ = "chunk_tags"
    __table_args__ = (
        CheckConstraint("source IN ('auto','manual','rule')", name="source"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence"),
    )
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    source: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float, server_default="1")
