from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedMixin, IdentityMixin, UpdatedMixin


class ProcessingTask(UpdatedMixin, Base):
    __tablename__ = "processing_tasks"
    __table_args__ = (
        CheckConstraint("task_type IN ('ingest','reindex','sync_payload','delete')", name="task_type"),
        CheckConstraint("status IN ('pending','running','succeeded','failed')", name="status"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress"),
        Index("idx_processing_tasks_status_time", "status", "created_at"),
        Index("idx_processing_tasks_doc", "doc_id", "created_at"),
    )
    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    task_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    stage: Mapped[str | None] = mapped_column(String(30))
    progress: Mapped[int] = mapped_column(Integer, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RelevanceJudgment(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "relevance_judgments"
    __table_args__ = (
        CheckConstraint("judgment IN (0,1,2)", name="judgment"),
        CheckConstraint("judge_type IN ('human','auto')", name="judge_type"),
    )
    query: Mapped[str] = mapped_column(Text)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"))
    judgment: Mapped[int] = mapped_column(Integer)
    judge_type: Mapped[str] = mapped_column(String(20), server_default="human")
    judge_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)


class SystemSetting(UpdatedMixin, Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class RuntimeConfiguration(CreatedMixin, Base):
    __tablename__ = "runtime_configurations"
    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    index_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class RuntimeState(UpdatedMixin, Base):
    __tablename__ = "runtime_state"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    configuration_id: Mapped[PyUUID] = mapped_column(ForeignKey("runtime_configurations.id"))


class IndexRebuild(UpdatedMixin, CreatedMixin, Base):
    __tablename__ = "index_rebuilds"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','evaluating','ready','switched','failed','cancelled')",
            name="status",
        ),
        Index("idx_index_rebuilds_status_time", "status", "created_at"),
    )
    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_configuration_id: Mapped[PyUUID] = mapped_column(ForeignKey("runtime_configurations.id"))
    target_configuration_id: Mapped[PyUUID] = mapped_column(ForeignKey("runtime_configurations.id"))
    target_collection: Mapped[str] = mapped_column(String(100), unique=True)
    target_values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    target_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    total_documents: Mapped[int] = mapped_column(Integer, server_default="0")
    completed_documents: Mapped[int] = mapped_column(Integer, server_default="0")
    failed_documents: Mapped[int] = mapped_column(Integer, server_default="0")
    checkpoint_document_id: Mapped[int | None] = mapped_column(BigInteger)
    source_revision: Mapped[str] = mapped_column(String(64))
    finish_revision: Mapped[str | None] = mapped_column(String(64))
    evaluation_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_runs.id"))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationRun(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (CheckConstraint("dataset_kind IN ('tuning','frozen')", name="dataset_kind"),)
    name: Mapped[str] = mapped_column(String(200))
    dataset_version: Mapped[str] = mapped_column(String(100))
    dataset_kind: Mapped[str] = mapped_column(String(20), server_default="frozen")
    dataset_hash: Mapped[str] = mapped_column(String(64))
    configuration_id: Mapped[PyUUID] = mapped_column(ForeignKey("runtime_configurations.id"))
    collection: Mapped[str] = mapped_column(String(100))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    passed: Mapped[bool] = mapped_column(server_default="false")
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
