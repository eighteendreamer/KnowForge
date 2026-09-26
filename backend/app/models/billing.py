from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedMixin, IdentityMixin, UpdatedMixin


class BalanceTransaction(IdentityMixin, CreatedMixin, Base):
    """余额的唯一事实来源：余额按 account 汇总得出，不落列，避免绕过账本写入造成永久偏差。"""

    __tablename__ = "balance_transactions"
    __table_args__ = (
        CheckConstraint("amount_cent > 0", name="amount_positive"),
        Index("idx_balance_transactions_account_time", "account_id", "created_at"),
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount_cent: Mapped[int] = mapped_column(BigInteger)
    channel: Mapped[str] = mapped_column(String(30), server_default="manual")
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)


class RechargePackage(IdentityMixin, UpdatedMixin, Base):
    __tablename__ = "recharge_packages"
    __table_args__ = (
        CheckConstraint("amount_cent > 0", name="amount_positive"),
        CheckConstraint("bonus_cent >= 0", name="bonus_non_negative"),
    )
    label: Mapped[str] = mapped_column(String(100), unique=True)
    amount_cent: Mapped[int] = mapped_column(BigInteger)
    bonus_cent: Mapped[int] = mapped_column(BigInteger, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")


CHANNEL_TYPES = ("alipay", "wechat", "stripe", "custom")


class RechargeChannel(IdentityMixin, UpdatedMixin, Base):
    __tablename__ = "recharge_channels"
    __table_args__ = (
        CheckConstraint("code <> ''", name="code_not_blank"),
        CheckConstraint("channel_type IN ('alipay', 'wechat', 'stripe', 'custom')", name="channel_type"),
    )
    code: Mapped[str] = mapped_column(String(30), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    # 类型决定要收哪些凭据、能不能在线下单；商户号之类的标识降级成该类型的一个普通字段。
    channel_type: Mapped[str] = mapped_column(String(20), server_default="custom")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")


class RechargeChannelCredential(IdentityMixin, UpdatedMixin, Base):
    """渠道凭据按字段存密文。

    一个厂商要 2-5 把密钥（微信：商户私钥 + APIv3 密钥 + 序列号 + 回调地址；支付宝证书模式还要三证），
    塞进一列就再也回答不了"缺哪一项、哪把在用、什么时候换的"，所以一个键一行、各自带版本和指纹。
    """

    __tablename__ = "recharge_channel_credentials"
    __table_args__ = (
        CheckConstraint("key_version >= 1", name="version_positive"),
        # 命名约定取第一列，得到 uq_recharge_channel_credentials_channel_id；迁移里必须用同一个名字。
        UniqueConstraint("channel_id", "key_name"),
    )
    channel_id: Mapped[int] = mapped_column(ForeignKey("recharge_channels.id", ondelete="CASCADE"))
    key_name: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(100))
    # 落库时的"是否密钥"快照：规格以后改了也不会让旧行的回显语义漂移。
    secret_class: Mapped[bool] = mapped_column(Boolean, server_default="true")
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, server_default="1")
    fingerprint: Mapped[str] = mapped_column(String(64))
    set_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
