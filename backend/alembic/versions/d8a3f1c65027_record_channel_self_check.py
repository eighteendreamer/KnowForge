"""给支付渠道加上"最近一次连通性自检"的结论。

配置项齐全不等于厂商认这把钥匙：支付宝公钥拷错时字段是全的、请求也发得出去，只有响应验签会失败。
这个状态必须由服务端记住，否则列表上的"配置完整"又是一个会被误读成"能收款"的假信号。
"""

import sqlalchemy as sa
from alembic import op

revision = "d8a3f1c65027"
down_revision = "b5c7e2f4a913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recharge_channels", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    # 三态：NULL=从未自检，false=自检未通过，true=自检通过。用 bool 而不是字符串枚举，
    # 因为"未自检"和"未通过"的差别只在于有没有跑过，不需要第四个状态。
    op.add_column("recharge_channels", sa.Column("verify_passed", sa.Boolean(), nullable=True))
    op.add_column("recharge_channels", sa.Column("verify_detail", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("recharge_channels", "verify_detail")
    op.drop_column("recharge_channels", "verify_passed")
    op.drop_column("recharge_channels", "verified_at")
