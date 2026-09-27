from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    # 在线入账指回订单，人工入账留空：这样"这笔钱是谁确认收到的"永远可查。
    payment_order_id: Mapped[int | None] = mapped_column(ForeignKey("payment_orders.id"))


class PaymentOrder(IdentityMixin, UpdatedMixin, Base):
    """在线充值订单。

    钱到没到只认厂商的回调与查单结果，不认"用户点了支付"；`out_trade_no` 是我方生成的商户单号，
    也是幂等锚点——厂商回过来的单号可能缺失或为空，不能拿它当唯一约束。
    """

    __tablename__ = "payment_orders"
    __table_args__ = (
        CheckConstraint("amount_cent > 0", name="amount_positive"),
        CheckConstraint("bonus_cent >= 0", name="bonus_non_negative"),
        # 折扣不能把单子抹到 0：厂商侧最小金额是 1 分，0 元单也失去"钱确实经过厂商"这层证明。
        CheckConstraint("payable_cent > 0", name="payable_positive"),
        CheckConstraint("discount_cent >= 0", name="discount_non_negative"),
        CheckConstraint("discount_cent < amount_cent", name="discount_below_amount"),
        CheckConstraint("status IN ('created', 'pending', 'paid', 'expired', 'failed')", name="status"),
        Index("idx_payment_orders_account_time", "account_id", "created_at"),
        # 关单与对账都按 (status, expires_at) 扫，没有这个索引每轮都要全表扫。
        Index("idx_payment_orders_status_expiry", "status", "expires_at"),
    )
    out_trade_no: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("recharge_channels.id"))
    package_id: Mapped[int | None] = mapped_column(ForeignKey("recharge_packages.id"))
    # 金额与赠送在下单时冻结成快照：档位后来改了也不影响已经付过钱的这一单该入账多少。
    amount_cent: Mapped[int] = mapped_column(BigInteger)
    bonus_cent: Mapped[int] = mapped_column(BigInteger, server_default="0")
    # 促销码只改实付：payable_cent 是发给厂商的金额，也是入账时核对回执金额的基准。
    payable_cent: Mapped[int] = mapped_column(BigInteger)
    discount_cent: Mapped[int] = mapped_column(BigInteger, server_default="0")
    promo_code_id: Mapped[int | None] = mapped_column(ForeignKey("recharge_promo_codes.id"))
    currency: Mapped[str] = mapped_column(String(3), server_default="CNY")
    status: Mapped[str] = mapped_column(String(20), server_default="created")
    provider_trade_no: Mapped[str | None] = mapped_column(String(64))
    code_url: Mapped[str | None] = mapped_column(Text)
    redirect_url: Mapped[str | None] = mapped_column(Text)
    notify_digest: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentOrderEvent(IdentityMixin, CreatedMixin, Base):
    """订单时间线。审计表是安全日志，不该被"这单走到哪一步了"这种产品视图反向消费。"""

    __tablename__ = "payment_order_events"
    __table_args__ = (Index("idx_payment_order_events_order_time", "order_id", "created_at"),)
    order_id: Mapped[int] = mapped_column(ForeignKey("payment_orders.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40))
    # 只放摘要与白名单标量（厂商返回码、金额、CAS 结果），原始回调报文与密钥一律不进这里。
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")


class RechargePromoCode(IdentityMixin, UpdatedMixin, Base):
    """促销码。抵扣只改"实付多少"，不改"到账多少"——活动让掉的是收单的钱，不是给用户的额度。

    码本身按大写存（`code = upper(code)` 由 CHECK 兜住），比对时不再做大小写特判。
    """

    __tablename__ = "recharge_promo_codes"
    __table_args__ = (
        CheckConstraint("code = upper(code)", name="code_uppercase"),
        CheckConstraint("code <> ''", name="code_not_blank"),
        CheckConstraint("kind IN ('amount_off', 'percent')", name="kind"),
        CheckConstraint(
            "(kind = 'percent' AND value >= 1 AND value <= 90) OR (kind = 'amount_off' AND value > 0)",
            name="value_within_kind",
        ),
        CheckConstraint("min_amount_cent >= 0", name="min_amount_non_negative"),
        CheckConstraint("used_count >= 0", name="used_count_non_negative"),
    )
    code: Mapped[str] = mapped_column(String(32), unique=True)
    label: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16), server_default="amount_off")
    # amount_off 是"立减多少分"，percent 是"打几折的百分比整数"，同一个列两种含义靠 kind 区分。
    value: Mapped[int] = mapped_column(Integer)
    min_amount_cent: Mapped[int] = mapped_column(BigInteger, server_default="0")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int | None] = mapped_column(Integer)
    per_account_limit: Mapped[int | None] = mapped_column(Integer)
    # 只在订单真的入账时才 +1：下单不占额度，否则放弃付款会把活动量吃光。
    used_count: Mapped[int] = mapped_column(Integer, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")


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
    # 最近一次连通性自检的结论。"字段齐了"和"厂商认这把钥匙"是两件事，列表必须能区分这两者。
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verify_passed: Mapped[bool | None] = mapped_column(Boolean)
    verify_detail: Mapped[str | None] = mapped_column(String(300))


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
