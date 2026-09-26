from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedMixin, IdentityMixin, UpdatedMixin


class Account(IdentityMixin, UpdatedMixin, Base):
    """唯一的账号表：管理端两类角色与门户注册的 end_user 共用一张表和同一个 owner_id 归属。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('super_admin', 'content_admin', 'end_user')", name="role"),
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
    )
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), server_default="active")


class ApiKey(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled', 'revoked')", name="status"),
        CheckConstraint("rate_limit_per_day > 0 AND rate_limit_per_minute > 0", name="limits"),
    )
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{knowledge:read}")
    rate_limit_per_day: Mapped[int] = mapped_column(Integer, server_default="1000")
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, server_default="60")
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_calls: Mapped[int] = mapped_column(BigInteger, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiLog(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "api_logs"
    __table_args__ = (Index("idx_api_logs_key_time", "api_key_id", "created_at"),)
    request_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True))
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"))
    endpoint: Mapped[str] = mapped_column(String(100))
    method: Mapped[str] = mapped_column(String(10))
    query: Mapped[str | None] = mapped_column(Text)
    search_type: Mapped[str | None] = mapped_column(String(20))
    top_k: Mapped[int | None] = mapped_column(Integer)
    result_count: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status_code: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)


class AuditLog(IdentityMixin, CreatedMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("idx_audit_logs_operator_time", "operator_id", "created_at"),)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(100))
    client_ip: Mapped[str | None] = mapped_column(INET)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
