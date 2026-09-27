"""加上在线充值订单与订单事件两张表，并让账本能指回订单。

在线收款必须有"我方单号"这一层：厂商回调可能重复、乱序、甚至永远不来，
幂等只能锚在自己生成的 out_trade_no 上，所以它建唯一约束，而厂商返回的单号只建普通索引。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f2c7a05d18b4"
down_revision = "d8a3f1c65027"
branch_labels = None
depends_on = None

ORDERS = "payment_orders"
EVENTS = "payment_order_events"


def upgrade() -> None:
    op.create_table(
        ORDERS,
        sa.Column("out_trade_no", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("package_id", sa.BigInteger(), nullable=True),
        sa.Column("amount_cent", sa.BigInteger(), nullable=False),
        sa.Column("bonus_cent", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="CNY", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="created", nullable=False),
        sa.Column("provider_trade_no", sa.String(length=64), nullable=True),
        sa.Column("code_url", sa.Text(), nullable=True),
        sa.Column("redirect_url", sa.Text(), nullable=True),
        sa.Column("notify_digest", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_cent > 0", name=op.f(f"ck_{ORDERS}_amount_positive")),
        sa.CheckConstraint("bonus_cent >= 0", name=op.f(f"ck_{ORDERS}_bonus_non_negative")),
        sa.CheckConstraint(
            "status IN ('created', 'pending', 'paid', 'expired', 'failed')", name=op.f(f"ck_{ORDERS}_status")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["users.id"], name=op.f(f"fk_{ORDERS}_account_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["recharge_channels.id"], name=op.f(f"fk_{ORDERS}_channel_id_recharge_channels")
        ),
        sa.ForeignKeyConstraint(
            ["package_id"], ["recharge_packages.id"], name=op.f(f"fk_{ORDERS}_package_id_recharge_packages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{ORDERS}")),
        sa.UniqueConstraint("out_trade_no", name=op.f(f"uq_{ORDERS}_out_trade_no")),
    )
    op.create_index(
        "idx_payment_orders_account_time", ORDERS, ["account_id", "created_at"], unique=False
    )
    op.create_index("idx_payment_orders_status_expiry", ORDERS, ["status", "expires_at"], unique=False)

    op.create_table(
        EVENTS,
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            [f"{ORDERS}.id"],
            name=op.f(f"fk_{EVENTS}_order_id_payment_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{EVENTS}")),
    )
    op.create_index("idx_payment_order_events_order_time", EVENTS, ["order_id", "created_at"], unique=False)

    # 先建订单表再挂这条外键，顺序反了 Postgres 会找不到引用表。
    op.add_column("balance_transactions", sa.Column("payment_order_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_balance_transactions_payment_order_id_payment_orders"),
        "balance_transactions",
        ORDERS,
        ["payment_order_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_balance_transactions_payment_order_id_payment_orders"),
        "balance_transactions",
        type_="foreignkey",
    )
    op.drop_column("balance_transactions", "payment_order_id")
    op.drop_index("idx_payment_order_events_order_time", table_name=EVENTS)
    op.drop_table(EVENTS)
    op.drop_index("idx_payment_orders_status_expiry", table_name=ORDERS)
    op.drop_index("idx_payment_orders_account_time", table_name=ORDERS)
    op.drop_table(ORDERS)
