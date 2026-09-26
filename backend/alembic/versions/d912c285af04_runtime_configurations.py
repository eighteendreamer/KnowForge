import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d912c285af04"
down_revision = "e0b801873372"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_configurations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("index_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "index_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_runtime_configurations_index_fingerprint")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            name=op.f("fk_runtime_configurations_created_by_admin_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_configurations")),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("dataset_version", sa.String(100), nullable=False),
        sa.Column("dataset_kind", sa.String(20), server_default="frozen", nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("configuration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection", sa.String(100), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("passed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("dataset_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_evaluation_runs_dataset_hash")),
        sa.CheckConstraint(
            "dataset_kind IN ('tuning', 'frozen')", name=op.f("ck_evaluation_runs_dataset_kind")
        ),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["runtime_configurations.id"],
            name=op.f("fk_evaluation_runs_configuration_id_runtime_configurations"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["admin_users.id"], name=op.f("fk_evaluation_runs_created_by_admin_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
    )
    op.create_table(
        "index_rebuilds",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_configuration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_configuration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_collection", sa.String(100), nullable=False),
        sa.Column("target_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("total_documents", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_documents", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_documents", sa.Integer(), server_default="0", nullable=False),
        sa.Column("checkpoint_document_id", sa.BigInteger(), nullable=True),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("finish_revision", sa.String(64), nullable=True),
        sa.Column("evaluation_id", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','running','evaluating','ready','switched','failed','cancelled')",
            name=op.f("ck_index_rebuilds_status"),
        ),
        sa.CheckConstraint(
            "target_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_index_rebuilds_target_fingerprint")
        ),
        sa.ForeignKeyConstraint(
            ["source_configuration_id"],
            ["runtime_configurations.id"],
            name=op.f("fk_index_rebuilds_source_configuration_id_runtime_configurations"),
        ),
        sa.ForeignKeyConstraint(
            ["target_configuration_id"],
            ["runtime_configurations.id"],
            name=op.f("fk_index_rebuilds_target_configuration_id_runtime_configurations"),
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluation_runs.id"],
            name=op.f("fk_index_rebuilds_evaluation_id_evaluation_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_rebuilds")),
        sa.UniqueConstraint("target_collection", name=op.f("uq_index_rebuilds_target_collection")),
    )
    op.create_index(
        "idx_index_rebuilds_status_time", "index_rebuilds", ["status", "created_at"], unique=False
    )
    op.create_table(
        "runtime_state",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("configuration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_runtime_state_singleton")),
        sa.ForeignKeyConstraint(
            ["configuration_id"],
            ["runtime_configurations.id"],
            name=op.f("fk_runtime_state_configuration_id_runtime_configurations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_state")),
    )


def downgrade() -> None:
    op.drop_table("runtime_state")
    op.drop_index("idx_index_rebuilds_status_time", table_name="index_rebuilds")
    op.drop_table("index_rebuilds")
    op.drop_table("evaluation_runs")
    op.drop_table("runtime_configurations")
