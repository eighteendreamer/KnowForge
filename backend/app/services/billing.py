from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BalanceTransaction,
    PaymentOrder,
    RechargeChannel,
    RechargePackage,
    RechargePromoCode,
)
from app.services.payments import credentials as channel_credentials


async def balance_cent(session: AsyncSession, account_id: int) -> int:
    """余额只由流水汇总得出：不落列就不存在账本与余额不一致的可能。"""
    total = await session.scalar(
        select(func.coalesce(func.sum(BalanceTransaction.amount_cent), 0)).where(
            BalanceTransaction.account_id == account_id
        )
    )
    return int(total or 0)


async def balances_by_account(session: AsyncSession) -> dict[int, int]:
    rows = await session.execute(
        select(BalanceTransaction.account_id, func.sum(BalanceTransaction.amount_cent)).group_by(
            BalanceTransaction.account_id
        )
    )
    return {account_id: int(total) for account_id, total in rows}


def transaction_view(row: BalanceTransaction) -> dict[str, Any]:
    return {
        "id": row.id,
        "amount_cent": row.amount_cent,
        "channel": row.channel,
        "operator_id": row.operator_id,
        "note": row.note,
        "created_at": row.created_at,
    }


def package_view(row: RechargePackage) -> dict[str, Any]:
    return {
        "id": row.id,
        "label": row.label,
        "amount_cent": row.amount_cent,
        "bonus_cent": row.bonus_cent,
        "enabled": row.enabled,
        "sort_order": row.sort_order,
    }


def order_view(row: PaymentOrder, channel_name: str = "") -> dict[str, Any]:
    """订单对外视图。付款要用的 code_url / redirect_url 只在未付时有意义，付完就不必再回传。"""
    settled = row.status == "paid"
    return {
        "out_trade_no": row.out_trade_no,
        "status": row.status,
        "channel_name": channel_name,
        "amount_cent": row.amount_cent,
        "bonus_cent": row.bonus_cent,
        # 折扣与实付单独回显：门户结算页要显示"促销抵扣"，只给原价就会让人以为算错了。
        "discount_cent": row.discount_cent,
        "payable_cent": row.payable_cent,
        "credited_cent": row.amount_cent + row.bonus_cent if settled else 0,
        "currency": row.currency,
        "code_url": None if settled else row.code_url,
        "redirect_url": None if settled else row.redirect_url,
        "provider_trade_no": row.provider_trade_no,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "paid_at": row.paid_at,
    }


def promo_view(row: RechargePromoCode) -> dict[str, Any]:
    """促销码回显。`remaining` 由已用/总量现算，管理列表要直接看到还剩多少名额。"""
    return {
        "id": row.id,
        "code": row.code,
        "label": row.label,
        "kind": row.kind,
        "value": row.value,
        "min_amount_cent": row.min_amount_cent,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "max_uses": row.max_uses,
        "remaining_uses": None if row.max_uses is None else max(0, row.max_uses - row.used_count),
        "per_account_limit": row.per_account_limit,
        "used_count": row.used_count,
        "enabled": row.enabled,
        "updated_at": row.updated_at,
    }


def channel_view(row: RechargeChannel, credentials: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """渠道视图。密钥只回显指纹与版本，配置完整性由已存的键名直接推导，不给调用方漏传的机会。"""
    return {
        "id": row.id,
        "code": row.code,
        "display_name": row.display_name,
        "channel_type": row.channel_type,
        "enabled": row.enabled,
        "updated_at": row.updated_at,
        "credentials": list(credentials),
        "configuration": channel_credentials.state(row.channel_type, [item["key"] for item in credentials]),
        # 自检结论与"字段齐不齐"分开报：前者是厂商认不认，后者只是我们收没收回。
        "verification": {
            "checked": row.verified_at is not None,
            "passed": bool(row.verify_passed),
            "at": row.verified_at,
            "detail": row.verify_detail,
        },
    }
