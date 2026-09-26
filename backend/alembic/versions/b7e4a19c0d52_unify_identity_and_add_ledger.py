"""Unify the identity domain under ``users`` and add the recharge ledger tables.

``admin_users`` is renamed to ``users`` so portal registrations, ``api_keys.owner_id`` and
audit operators all resolve against one table instead of a second identity hierarchy.
Constraint names are renamed explicitly because the metadata naming convention derives them
from the table name; without these renames ``alembic check`` reports schema drift.
"""

import sqlalchemy as sa
from alembic import op

revision = "b7e4a19c0d52"
down_revision = "5f1c7be0a9d4"
branch_labels = None
depends_on = None

# (所属表, 旧名, 新名)。外键约束挂在引用表上，不在被引用的 users 表上，所以必须逐条带表名。
RENAMED_CONSTRAINTS = [
    ("users", "pk_admin_users", "pk_users"),
    ("users", "uq_admin_users_username", "uq_users_username"),
    ("users", "ck_admin_users_role", "ck_users_role"),
    ("users", "ck_admin_users_status", "ck_users_status"),
    ("api_keys", "fk_api_keys_owner_id_admin_users", "fk_api_keys_owner_id_users"),
    ("audit_logs", "fk_audit_logs_operator_id_admin_users", "fk_audit_logs_operator_id_users"),
    ("documents", "fk_documents_uploader_id_admin_users", "fk_documents_uploader_id_users"),
    ("system_settings", "fk_system_settings_updated_by_admin_users", "fk_system_settings_updated_by_users"),
    (
        "relevance_judgments",
        "fk_relevance_judgments_judge_id_admin_users",
        "fk_relevance_judgments_judge_id_users",
    ),
    (
        "runtime_configurations",
        "fk_runtime_configurations_created_by_admin_users",
        "fk_runtime_configurations_created_by_users",
    ),
    ("evaluation_runs", "fk_evaluation_runs_created_by_admin_users", "fk_evaluation_runs_created_by_users"),
]

ROLE_CHECK_BODY = "role IN ('super_admin', 'content_admin', 'end_user')"
ROLE_CHECK_ADMIN_ONLY_BODY = "role IN ('super_admin', 'content_admin')"


def _rename(table: str, old: str, new: str) -> None:
    op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT "{old}" TO "{new}"')


def upgrade() -> None:
    op.rename_table("admin_users", "users")
    for table, old, new in RENAMED_CONSTRAINTS:
        _rename(table, old, new)

    # op.f 标记名字已是最终形式；不带它时 alembic 会再套一层命名约定，得到 ck_users_ck_users_role。
    op.drop_constraint(op.f("ck_users_role"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_role"), "users", ROLE_CHECK_BODY)

    op.create_table(
        "balance_transactions",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("amount_cent", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=30), server_default="manual", nullable=False),
        sa.Column("operator_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_cent > 0", name=op.f("ck_balance_transactions_amount_positive")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["users.id"], name=op.f("fk_balance_transactions_account_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["users.id"], name=op.f("fk_balance_transactions_operator_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_balance_transactions")),
    )
    op.create_index(
        "idx_balance_transactions_account_time",
        "balance_transactions",
        ["account_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "recharge_packages",
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("amount_cent", sa.BigInteger(), nullable=False),
        sa.Column("bonus_cent", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_cent > 0", name=op.f("ck_recharge_packages_amount_positive")),
        sa.CheckConstraint("bonus_cent >= 0", name=op.f("ck_recharge_packages_bonus_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recharge_packages")),
        sa.UniqueConstraint("label", name=op.f("uq_recharge_packages_label")),
    )

    op.create_table(
        "recharge_channels",
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("merchant_id", sa.String(length=200), nullable=True),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("code <> ''", name=op.f("ck_recharge_channels_code_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recharge_channels")),
        sa.UniqueConstraint("code", name=op.f("uq_recharge_channels_code")),
    )


def downgrade() -> None:
    op.drop_table("recharge_channels")
    op.drop_table("recharge_packages")
    op.drop_index("idx_balance_transactions_account_time", table_name="balance_transactions")
    op.drop_table("balance_transactions")

    # 门户账号存在时这条语句会因检查约束被违反而中止整个降级，不做静默清理。
    op.drop_constraint(op.f("ck_users_role"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_role"), "users", ROLE_CHECK_ADMIN_ONLY_BODY)

    for table, old, new in reversed(RENAMED_CONSTRAINTS):
        _rename(table, new, old)
    op.rename_table("users", "admin_users")
