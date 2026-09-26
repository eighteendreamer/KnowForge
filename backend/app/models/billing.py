from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text
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


class RechargeChannel(IdentityMixin, UpdatedMixin, Base):
    __tablename__ = "recharge_channels"
    __table_args__ = (CheckConstraint("code <> ''", name="code_not_blank"),)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    merchant_id: Mapped[str | None] = mapped_column(String(200))
    secret: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
