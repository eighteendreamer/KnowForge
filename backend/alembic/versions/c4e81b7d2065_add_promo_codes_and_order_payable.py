"""加上促销码表，并让订单记住"实付多少"。

订单原来只有 amount_cent（档位原价），加了折扣之后发给厂商的金额和对账基准都变了，
所以把实付单独存成 payable_cent 而不是在读取时现算：折扣规则以后改动不会让旧订单的金额漂移。
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e81b7d2065"
down_revision = "f2c7a05d18b4"
branch_labels = None
depends_on = None

PROMOS = "recharge_promo_codes"
ORDERS = "payment_orders"


def upgrade() -> None:
    op.create_table(
        PROMOS,
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="amount_off", nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("min_amount_cent", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("per_account_limit", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("code = upper(code)", name=op.f(f"ck_{PROMOS}_code_uppercase")),
        sa.CheckConstraint("code <> ''", name=op.f(f"ck_{PROMOS}_code_not_blank")),
        sa.CheckConstraint("kind IN ('amount_off', 'percent')", name=op.f(f"ck_{PROMOS}_kind")),
        sa.CheckConstraint(
            "(kind = 'percent' AND value >= 1 AND value <= 90) OR (kind = 'amount_off' AND value > 0)",
            name=op.f(f"ck_{PROMOS}_value_within_kind"),
        ),
        sa.CheckConstraint("min_amount_cent >= 0", name=op.f(f"ck_{PROMOS}_min_amount_non_negative")),
        sa.CheckConstraint("used_count >= 0", name=op.f(f"ck_{PROMOS}_used_count_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PROMOS}")),
        sa.UniqueConstraint("code", name=op.f(f"uq_{PROMOS}_code")),
    )

    # 已有订单先按"没有折扣"补齐实付金额：可空→回填→再收紧成非空，一步加非空列会在有数据的库上失败。
    op.add_column(ORDERS, sa.Column("payable_cent", sa.BigInteger(), nullable=True))
    op.add_column(ORDERS, sa.Column("discount_cent", sa.BigInteger(), server_default="0", nullable=False))
    op.execute(sa.text(f"UPDATE {ORDERS} SET payable_cent = amount_cent WHERE payable_cent IS NULL"))
    op.alter_column(ORDERS, "payable_cent", existing_type=sa.BigInteger(), nullable=False)
    op.add_column(ORDERS, sa.Column("promo_code_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f(f"fk_{ORDERS}_promo_code_id_{PROMOS}"), ORDERS, PROMOS, ["promo_code_id"], ["id"]
    )
    # 名字只给短式：naming_convention 会自己拼成 ck_<table>_<name>，这里手拼一遍会得到双前缀。
    op.create_check_constraint("payable_positive", ORDERS, "payable_cent > 0")
    op.create_check_constraint("discount_non_negative", ORDERS, "discount_cent >= 0")
    # 折扣必须留个正数尾款：0 元单在厂商侧下不出去，也让"钱确实经过厂商"这层证明失效。
    op.create_check_constraint("discount_below_amount", ORDERS, "discount_cent < amount_cent")


def downgrade() -> None:
    for name in ("discount_below_amount", "discount_non_negative", "payable_positive"):
        op.drop_constraint(op.f(f"ck_{ORDERS}_{name}"), ORDERS, type_="check")
    op.drop_constraint(op.f(f"fk_{ORDERS}_promo_code_id_{PROMOS}"), ORDERS, type_="foreignkey")
    op.drop_column(ORDERS, "promo_code_id")
    op.drop_column(ORDERS, "discount_cent")
    op.drop_column(ORDERS, "payable_cent")
    op.drop_table(PROMOS)
