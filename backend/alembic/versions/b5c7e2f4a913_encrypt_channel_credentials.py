"""把支付渠道从"一列明文密钥"改成按字段加密存储。

升级前必须先在 .env 配好 KNOFORGE_PAYMENT_MASTER_KEY：回填要把旧的 merchant_id/secret 加密成新表里的
凭据行，没有主密钥就无法写出可读的密文，所以这里选择直接失败而不是静默降级成明文落库。

降级只能回写旧两列表达得了的 legacy_secret 与商户标识，其余凭据行随表删除，需要重新录入。
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import Settings
from app.services.payments import crypto, specs

revision = "b5c7e2f4a913"
down_revision = "a3d9c51e7b48"
branch_labels = None
depends_on = None

CREDENTIALS = "recharge_channel_credentials"

# 旧 merchant_id 列在新规格里有对应的正规字段，能过校验就按正规键存，过不了才退回历史键，
# 这样"admin"这种填错的东西不会被当成商户号发给厂商。
MERCHANT_KEY_BY_TYPE = {"wechat": "mch_id", "alipay": "app_id"}
TYPE_BY_CODE_PREFIX = (
    ("wx", "wechat"),
    ("wechat", "wechat"),
    ("ali", "alipay"),
    ("zfb", "alipay"),
    ("stripe", "stripe"),
)


def _channel_type_for(code: str) -> str:
    lowered = (code or "").strip().lower()
    for prefix, channel_type in TYPE_BY_CODE_PREFIX:
        if lowered.startswith(prefix):
            return channel_type
    return "custom"


def _merchant_key(channel_type: str, value: str) -> str:
    key = MERCHANT_KEY_BY_TYPE.get(channel_type)
    if key is None:
        return "legacy_merchant_id"
    try:
        specs.validate_value(channel_type, key, value)
    except ValueError:
        return "legacy_merchant_id"
    return key


def _label_and_class(channel_type: str, key: str) -> tuple[str, bool]:
    return specs.describe(channel_type, key)


def _backfill(bind) -> None:
    master_key = crypto.require_master_key(Settings().payment_master_key_bytes)
    channels = sa.table(
        "recharge_channels",
        sa.column("id", sa.BigInteger),
        sa.column("code", sa.String),
        sa.column("merchant_id", sa.String),
        sa.column("secret", sa.Text),
        sa.column("channel_type", sa.String),
        sa.column("enabled", sa.Boolean),
    )
    credentials = sa.table(
        CREDENTIALS,
        sa.column("channel_id", sa.BigInteger),
        sa.column("key_name", sa.String),
        sa.column("label", sa.String),
        sa.column("secret_class", sa.Boolean),
        sa.column("ciphertext", sa.LargeBinary),
        sa.column("key_version", sa.Integer),
        sa.column("fingerprint", sa.String),
    )
    rows = bind.execute(sa.select(channels)).mappings().all()
    for row in rows:
        channel_type = _channel_type_for(row["code"])
        bind.execute(channels.update().where(channels.c.id == row["id"]).values(channel_type=channel_type))
        stored: dict[str, str] = {}
        if row["merchant_id"]:
            stored[_merchant_key(channel_type, row["merchant_id"])] = row["merchant_id"]
        if row["secret"]:
            stored["legacy_secret"] = row["secret"]
        for key, value in stored.items():
            label, secret_class = _label_and_class(channel_type, key)
            bind.execute(
                credentials.insert().values(
                    channel_id=row["id"],
                    key_name=key,
                    label=label,
                    secret_class=secret_class,
                    ciphertext=crypto.seal(master_key, row["id"], key, 1, value),
                    key_version=1,
                    fingerprint=crypto.fingerprint(value),
                )
            )
        incomplete = bool(specs.missing_required(channel_type, stored)) or (
            specs.configuration_error(channel_type, stored) is not None
        )
        if incomplete and row["enabled"]:
            # 旧的"已配置"只表示那列有字符串。按新规格它多半只配了一半，启用着下单会在厂商侧报错，所以先停用。
            bind.execute(channels.update().where(channels.c.id == row["id"]).values(enabled=False))


def upgrade() -> None:
    op.add_column(
        "recharge_channels",
        sa.Column("channel_type", sa.String(length=20), server_default="custom", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_recharge_channels_channel_type"),
        "recharge_channels",
        "channel_type IN ('alipay', 'wechat', 'stripe', 'custom')",
    )
    op.create_table(
        CREDENTIALS,
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("key_name", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("secret_class", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("set_by_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("key_version >= 1", name=op.f(f"ck_{CREDENTIALS}_version_positive")),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["recharge_channels.id"],
            name=op.f(f"fk_{CREDENTIALS}_channel_id_recharge_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["set_by_id"], ["users.id"], name=op.f(f"fk_{CREDENTIALS}_set_by_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{CREDENTIALS}")),
        # 命名约定取第一列，所以这里必须是 uq_..._channel_id，和模型侧推导出来的名字一致。
        sa.UniqueConstraint("channel_id", "key_name", name=op.f(f"uq_{CREDENTIALS}_channel_id")),
    )
    _backfill(op.get_bind())
    op.drop_column("recharge_channels", "merchant_id")
    op.drop_column("recharge_channels", "secret")


def downgrade() -> None:
    bind = op.get_bind()
    op.add_column("recharge_channels", sa.Column("merchant_id", sa.String(length=200), nullable=True))
    op.add_column("recharge_channels", sa.Column("secret", sa.Text(), nullable=True))
    # 降级要能读出旧值，所以主密钥同样不能少；解不开就中止，宁可降级失败也不写出不可读的明文列。
    master_key = crypto.require_master_key(Settings().payment_master_key_bytes)
    channels = sa.table(
        "recharge_channels",
        sa.column("id", sa.BigInteger),
        sa.column("code", sa.String),
        sa.column("merchant_id", sa.String),
        sa.column("secret", sa.Text),
    )
    credentials = sa.table(
        CREDENTIALS,
        sa.column("channel_id", sa.BigInteger),
        sa.column("key_name", sa.String),
        sa.column("key_version", sa.Integer),
        sa.column("ciphertext", sa.LargeBinary),
    )
    for row in bind.execute(sa.select(channels.c.id)).all():
        channel_id = row[0]
        for stored in bind.execute(
            sa.select(credentials).where(credentials.c.channel_id == channel_id)
        ).mappings():
            value = crypto.open_secret(
                master_key,
                channel_id,
                stored["key_name"],
                stored["key_version"],
                stored["ciphertext"],
                "凭据",
            )
            if stored["key_name"] == "legacy_secret":
                bind.execute(channels.update().where(channels.c.id == channel_id).values(secret=value))
            elif (
                stored["key_name"] in MERCHANT_KEY_BY_TYPE.values()
                or stored["key_name"] == "legacy_merchant_id"
            ):
                bind.execute(channels.update().where(channels.c.id == channel_id).values(merchant_id=value))
    op.drop_table(CREDENTIALS)
    op.drop_constraint(op.f("ck_recharge_channels_channel_type"), "recharge_channels", type_="check")
    op.drop_column("recharge_channels", "channel_type")
