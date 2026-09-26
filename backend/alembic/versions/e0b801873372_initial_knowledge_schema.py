import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e0b801873372"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "admin_users",
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('super_admin', 'content_admin')", name=op.f("ck_admin_users_role")),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_admin_users_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_users")),
        sa.UniqueConstraint("username", name=op.f("uq_admin_users_username")),
    )
    op.create_table(
        "categories",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name=op.f("ck_categories_parent_not_self")
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["categories.id"], name=op.f("fk_categories_parent_id_categories")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("path", name=op.f("uq_categories_path")),
    )
    op.create_index(op.f("ix_categories_parent_id"), "categories", ["parent_id"], unique=False)
    op.create_table(
        "tags",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("color", sa.String(length=20), server_default="#18A058", nullable=False),
        sa.Column("auto_generated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1", nullable=False),
        sa.Column("review_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "review_status IN ('pending','approved','rejected')", name=op.f("ck_tags_review_status")
        ),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name=op.f("ck_tags_confidence")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("name", name=op.f("uq_tags_name")),
        sa.UniqueConstraint("normalized_name", name=op.f("uq_tags_normalized_name")),
    )
    op.create_index(op.f("ix_tags_review_status"), "tags", ["review_status"], unique=False)
    op.create_table(
        "api_keys",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), server_default="{knowledge:read}", nullable=False),
        sa.Column("rate_limit_per_day", sa.Integer(), server_default="1000", nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), server_default="60", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_calls", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled', 'revoked')", name=op.f("ck_api_keys_status")),
        sa.CheckConstraint(
            "rate_limit_per_day > 0 AND rate_limit_per_minute > 0", name=op.f("ck_api_keys_limits")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["admin_users.id"], name=op.f("fk_api_keys_owner_id_admin_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
    )
    op.create_table(
        "audit_logs",
        sa.Column("operator_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=100), nullable=True),
        sa.Column("client_ip", postgresql.INET(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["admin_users.id"], name=op.f("fk_audit_logs_operator_id_admin_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index("idx_audit_logs_operator_time", "audit_logs", ["operator_id", "created_at"], unique=False)
    op.create_table(
        "documents",
        sa.Column("doc_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("file_type", sa.String(length=10), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("ast_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("uploader_id", sa.BigInteger(), nullable=True),
        sa.Column("upload_time", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("total_pages", sa.Integer(), nullable=True),
        sa.Column("total_chunks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("auto_category_id", sa.BigInteger(), nullable=True),
        sa.Column("manual_category_id", sa.BigInteger(), nullable=True),
        sa.Column("is_public", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("file_type IN ('pdf', 'html')", name=op.f("ck_documents_file_type")),
        sa.CheckConstraint(
            "status IN ('pending','parsing','chunking','embedding','tagging','indexing','ready','failed','deleting')",
            name=op.f("ck_documents_status"),
        ),
        sa.CheckConstraint("file_size > 0", name=op.f("ck_documents_file_size")),
        sa.ForeignKeyConstraint(
            ["auto_category_id"], ["categories.id"], name=op.f("fk_documents_auto_category_id_categories")
        ),
        sa.ForeignKeyConstraint(
            ["manual_category_id"], ["categories.id"], name=op.f("fk_documents_manual_category_id_categories")
        ),
        sa.ForeignKeyConstraint(
            ["uploader_id"], ["admin_users.id"], name=op.f("fk_documents_uploader_id_admin_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("doc_id", name=op.f("uq_documents_doc_id")),
    )
    op.create_index("idx_documents_status_time", "documents", ["status", "upload_time"], unique=False)
    op.create_index(op.f("ix_documents_auto_category_id"), "documents", ["auto_category_id"], unique=False)
    op.create_index(
        op.f("ix_documents_manual_category_id"), "documents", ["manual_category_id"], unique=False
    )
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["admin_users.id"], name=op.f("fk_system_settings_updated_by_admin_users")
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_settings")),
    )
    op.create_table(
        "api_logs",
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("api_key_id", sa.BigInteger(), nullable=True),
        sa.Column("endpoint", sa.String(length=100), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("search_type", sa.String(length=20), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["api_keys.id"], name=op.f("fk_api_logs_api_key_id_api_keys"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_logs")),
    )
    op.create_index("idx_api_logs_key_time", "api_logs", ["api_key_id", "created_at"], unique=False)
    op.create_table(
        "chunks",
        sa.Column("chunk_id", sa.String(length=100), nullable=False),
        sa.Column("doc_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_path", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_with_context", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("element_types", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("difficulty", sa.String(length=10), nullable=True),
        sa.Column("qdrant_point_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("difficulty IN ('初级','中级','高级')", name=op.f("ck_chunks_difficulty")),
        sa.CheckConstraint("chunk_index >= 0", name=op.f("ck_chunks_chunk_index")),
        sa.CheckConstraint(
            "page_end IS NULL OR (page_start IS NOT NULL AND page_end >= page_start)",
            name=op.f("ck_chunks_page_end"),
        ),
        sa.CheckConstraint("page_start IS NULL OR page_start >= 1", name=op.f("ck_chunks_page_start")),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name=op.f("fk_chunks_category_id_categories")
        ),
        sa.ForeignKeyConstraint(
            ["doc_id"], ["documents.id"], name=op.f("fk_chunks_doc_id_documents"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint("chunk_id", name=op.f("uq_chunks_chunk_id")),
        sa.UniqueConstraint("doc_id", "chunk_index", name=op.f("uq_chunks_doc_id")),
        sa.UniqueConstraint("qdrant_point_id", name=op.f("uq_chunks_qdrant_point_id")),
    )
    op.create_index(
        "idx_chunks_text_trgm",
        "chunks",
        ["text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
    op.create_index(op.f("ix_chunks_category_id"), "chunks", ["category_id"], unique=False)
    op.create_table(
        "document_tags",
        sa.Column("doc_id", sa.BigInteger(), nullable=False),
        sa.Column("tag_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source IN ('auto','manual','rule')", name=op.f("ck_document_tags_source")),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name=op.f("ck_document_tags_confidence")),
        sa.ForeignKeyConstraint(
            ["doc_id"], ["documents.id"], name=op.f("fk_document_tags_doc_id_documents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_document_tags_tag_id_tags"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("doc_id", "tag_id", name=op.f("pk_document_tags")),
    )
    op.create_index(op.f("ix_document_tags_tag_id"), "document_tags", ["tag_id"], unique=False)
    op.create_table(
        "processing_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("doc_id", sa.BigInteger(), nullable=False),
        sa.Column("task_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=True),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "config_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','running','succeeded','failed')", name=op.f("ck_processing_tasks_status")
        ),
        sa.CheckConstraint(
            "task_type IN ('ingest','reindex','sync_payload','delete')",
            name=op.f("ck_processing_tasks_task_type"),
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name=op.f("ck_processing_tasks_progress")),
        sa.ForeignKeyConstraint(
            ["doc_id"],
            ["documents.id"],
            name=op.f("fk_processing_tasks_doc_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_tasks")),
    )
    op.create_index("idx_processing_tasks_doc", "processing_tasks", ["doc_id", "created_at"], unique=False)
    op.create_index(
        "idx_processing_tasks_status_time", "processing_tasks", ["status", "created_at"], unique=False
    )
    op.create_table(
        "chunk_tags",
        sa.Column("chunk_id", sa.BigInteger(), nullable=False),
        sa.Column("tag_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source IN ('auto','manual','rule')", name=op.f("ck_chunk_tags_source")),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name=op.f("ck_chunk_tags_confidence")),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["chunks.id"], name=op.f("fk_chunk_tags_chunk_id_chunks"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_chunk_tags_tag_id_tags"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("chunk_id", "tag_id", name=op.f("pk_chunk_tags")),
    )
    op.create_index(op.f("ix_chunk_tags_tag_id"), "chunk_tags", ["tag_id"], unique=False)
    op.create_table(
        "relevance_judgments",
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("chunk_id", sa.BigInteger(), nullable=False),
        sa.Column("judgment", sa.Integer(), nullable=False),
        sa.Column("judge_type", sa.String(length=20), server_default="human", nullable=False),
        sa.Column("judge_id", sa.BigInteger(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("judge_type IN ('human','auto')", name=op.f("ck_relevance_judgments_judge_type")),
        sa.CheckConstraint("judgment IN (0,1,2)", name=op.f("ck_relevance_judgments_judgment")),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_relevance_judgments_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["judge_id"], ["admin_users.id"], name=op.f("fk_relevance_judgments_judge_id_admin_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relevance_judgments")),
    )


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("relevance_judgments")
    op.drop_index(op.f("ix_chunk_tags_tag_id"), table_name="chunk_tags")
    op.drop_table("chunk_tags")
    op.drop_index("idx_processing_tasks_status_time", table_name="processing_tasks")
    op.drop_index("idx_processing_tasks_doc", table_name="processing_tasks")
    op.drop_table("processing_tasks")
    op.drop_index(op.f("ix_document_tags_tag_id"), table_name="document_tags")
    op.drop_table("document_tags")
    op.drop_index(op.f("ix_chunks_category_id"), table_name="chunks")
    op.drop_index(
        "idx_chunks_text_trgm",
        table_name="chunks",
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
    op.drop_table("chunks")
    op.drop_index("idx_api_logs_key_time", table_name="api_logs")
    op.drop_table("api_logs")
    op.drop_table("system_settings")
    op.drop_index(op.f("ix_documents_manual_category_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_auto_category_id"), table_name="documents")
    op.drop_index("idx_documents_status_time", table_name="documents")
    op.drop_table("documents")
    op.drop_index("idx_audit_logs_operator_time", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("api_keys")
    op.drop_index(op.f("ix_tags_review_status"), table_name="tags")
    op.drop_table("tags")
    op.drop_index(op.f("ix_categories_parent_id"), table_name="categories")
    op.drop_table("categories")
    op.drop_table("admin_users")
