from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BalanceTransaction, RechargeChannel, RechargePackage


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


def channel_view(row: RechargeChannel) -> dict[str, Any]:
    # 渠道密钥只写不读：管理端也只看有没有配过，不回显任何片段。
    return {
        "id": row.id,
        "code": row.code,
        "display_name": row.display_name,
        "merchant_id": row.merchant_id,
        "secret_configured": row.secret is not None,
        "enabled": row.enabled,
        "updated_at": row.updated_at,
    }
