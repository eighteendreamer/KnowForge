"""订单收尾：关掉到期未付的单，并在关单前向厂商确认一次。

只按本地时钟关单会把"用户在第 29 分钟付了钱"误判成超时；查一遍厂商才敢关。
厂商答不上来（网络、限流、单号还没落库）时照样按时间关单，但计数分开报，
因为 settlement 允许 expired→paid，迟到的回执仍然能补入账，不会把钱弄丢。
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.models import PaymentOrder, PaymentOrderEvent, RechargeChannel
from app.services.payments import credentials as channel_credentials
from app.services.payments import settlement
from app.services.payments.providers import registry
from app.services.payments.providers.base import ProviderError

BATCH = 50
OPEN_STATUSES = ("created", "pending")

# 已经挂给人查的单不再每轮重查厂商：那会把对账变成无限循环的重试。
awaiting_review = exists(
    select(1).where(
        PaymentOrderEvent.order_id == PaymentOrder.id,
        PaymentOrderEvent.kind == settlement.MISMATCH,
    )
)


async def close_due_orders(
    session: AsyncSession, master: bytes | None, *, now: datetime, limit: int = BATCH
) -> dict[str, int]:
    orders = list(
        await session.scalars(
            select(PaymentOrder)
            .where(
                PaymentOrder.status.in_(OPEN_STATUSES),
                PaymentOrder.expires_at <= now,
                ~awaiting_review,
            )
            .order_by(PaymentOrder.id)
            .limit(limit)
        )
    )
    channels = {
        row.id: row
        for row in await session.scalars(
            select(RechargeChannel).where(
                RechargeChannel.id.in_({order.channel_id for order in orders} or {0})
            )
        )
    }
    summary = {"expired": 0, "credited": 0, "unverified": 0, "held": 0}
    for order in orders:
        channel = channels.get(order.channel_id)
        values: dict[str, str] = {}
        if master and channel is not None and registry.supports_orders(channel.channel_type):
            values = await channel_credentials.load_values(session, master, channel.id, include_secrets=True)
        await session.commit()
        state = None
        if values and channel is not None:
            try:
                state = await registry.query_order(channel.channel_type, values, order.out_trade_no)
            except (ProviderError, httpx.HTTPError):
                # 厂商问不到答案不等于没付：照样按时间关单，但单独计数，因为它值得看一眼。
                # settlement 允许 expired→paid，所以真迟到的回执仍然能补入账，不会把钱弄丢。
                state = None
        if state is not None and state.paid and channel is not None:
            result = await settlement.mark_paid_and_credit(
                session,
                order,
                source=channel.code,
                received_cent=state.amount_cent,
                provider_trade_no=state.provider_trade_no,
                note="超时前对账查单",
            )
            summary["credited" if result.ok else "held"] += 1
        else:
            order.status = "expired"
            order.closed_at = now
            await settlement.record_event(
                session,
                order.id,
                "expired",
                {"verified": state is not None, "detail": (state.detail if state else "未向厂商确认")[:300]},
            )
            summary["expired" if state is not None else "unverified"] += 1
        await session.commit()
    return summary


async def review_queue(session: AsyncSession, limit: int = 50) -> Sequence[PaymentOrder]:
    """金额对不上、等着人来判的单。"""
    return list(
        await session.scalars(
            select(PaymentOrder)
            .where(awaiting_review, PaymentOrder.status != "paid")
            .order_by(PaymentOrder.id.desc())
            .limit(limit)
        )
    )


async def reconcile(settings: Settings) -> dict[str, Any]:
    engine = make_engine(settings)
    try:
        async with make_session_factory(engine)() as session:
            closed = await close_due_orders(session, settings.payment_master_key_bytes, now=datetime.now(UTC))
            closed["awaiting_review"] = len(await review_queue(session))
            return closed
    finally:
        await engine.dispose()
