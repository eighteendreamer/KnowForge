"""订单入账的唯一入口。

回调会重投、查单会和回调竞速、人工补单也可能再确认一次，所以"确认收到钱"这件事必须只有一个写法：
先锁订单行、看清它当前是什么状态，只有还没入账的这一次才允许往账本追加流水并把它翻成 paid。
余额本身按流水汇总、没有读改写，因此不需要锁账号行，串行的语义全在这条订单行的锁上。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BalanceTransaction, PaymentOrder, PaymentOrderEvent, RechargeChannel

CREDITED = "credited"
DUPLICATE = "duplicate"
MISMATCH = "amount_mismatch"

# 只有已经是 paid 的订单算重复通知；其余状态都还能被"厂商说钱到了"翻过来，
# 包括本地已过期或已关单的情况——用户真付了钱不能因为我们先超时就不入账。
OPEN_STATUSES = ("created", "pending", "expired", "failed")


@dataclass(frozen=True)
class Settlement:
    """入账结论。ok 为假时账本一定没动过，调用方可以安全地按结果决定回给厂商什么。"""

    outcome: str
    order_id: int
    status: str
    credited_cent: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == CREDITED


async def find_by_out_trade_no(session: AsyncSession, out_trade_no: str) -> PaymentOrder | None:
    if not out_trade_no:
        return None
    return await session.scalar(select(PaymentOrder).where(PaymentOrder.out_trade_no == out_trade_no))


async def record_event(
    session: AsyncSession, order_id: int, kind: str, detail: dict[str, Any] | None = None
) -> None:
    """订单时间线。detail 只放摘要与白名单标量，原始回调报文不进这里。"""
    session.add(PaymentOrderEvent(order_id=order_id, kind=kind, detail=detail or {}))


async def mark_paid_and_credit(
    session: AsyncSession,
    order: PaymentOrder,
    *,
    source: str,
    received_cent: int | None = None,
    provider_trade_no: str | None = None,
    notify_digest: str | None = None,
    note: str = "",
) -> Settlement:
    """把订单翻成 paid 并追加一行流水；抢到跃迁才入账。

    source 是渠道 code（写进账本，回答"这笔钱从哪个通道来的"），note 记录是谁确认的（回调/查单/人工）。
    """
    if received_cent is not None and received_cent != order.amount_cent:
        # 金额对不上多半是套错了单号或厂商侧改了价，这种单子必须留给人查，不能按我们的记录入账。
        await record_event(
            session,
            order.id,
            MISMATCH,
            {"expected_cent": order.amount_cent, "received_cent": received_cent, "by": source},
        )
        return Settlement(MISMATCH, order.id, order.status, detail="厂商回执金额与订单不一致，已挂起待人工核对")

    now = datetime.now(UTC)
    # 先锁订单行再判状态：回调重投和查单竞速会在两个事务里同时到达，
    # SELECT ... FOR UPDATE 让后到的那个必须等前一个提交，读到的是已翻成 paid 的状态。
    previous = await session.scalar(
        select(PaymentOrder.status).where(PaymentOrder.id == order.id).with_for_update()
    )
    if previous not in OPEN_STATUSES:
        await record_event(
            session,
            order.id,
            "duplicate_notify",
            {"by": source, "status": previous, "digest": notify_digest},
        )
        return Settlement(DUPLICATE, order.id, previous or "", detail="该订单已入账，重复通知不再记账")

    await session.execute(
        update(PaymentOrder)
        .where(PaymentOrder.id == order.id)
        .values(
            status="paid",
            updated_at=now,
            paid_at=func.coalesce(PaymentOrder.paid_at, now),
            provider_trade_no=func.coalesce(provider_trade_no, PaymentOrder.provider_trade_no),
            notify_digest=func.coalesce(notify_digest, PaymentOrder.notify_digest),
        )
        .execution_options(synchronize_session=False)
    )
    await session.refresh(order)
    channel = await session.scalar(select(RechargeChannel.code).where(RechargeChannel.id == order.channel_id))
    credited = order.amount_cent + order.bonus_cent
    entry = BalanceTransaction(
        account_id=order.account_id,
        amount_cent=credited,
        channel=channel or "online",
        payment_order_id=order.id,
        note=_ledger_note(order, previous, note),
    )
    session.add(entry)
    await session.flush()
    await record_event(
        session,
        order.id,
        "credited",
        {
            "by": source,
            "amount_cent": order.amount_cent,
            "bonus_cent": order.bonus_cent,
            "credited_cent": credited,
            "from_status": previous,
        },
    )
    return Settlement(
        CREDITED,
        order.id,
        order.status,
        credited_cent=credited,
        detail=(
            "已入账"
            if previous in ("created", "pending")
            else "订单先前已关闭，按厂商实收补入账并留痕"
        ),
    )


def _yuan(amount_cent: int) -> str:
    return f"{amount_cent // 100}.{amount_cent % 100:02d}元"


def _ledger_note(order: PaymentOrder, previous: str, note: str) -> str:
    """账本备注要能自解释：金额构成和确认来源写在一行里，省掉一次回表查订单。"""
    parts = [f"在线充值 {_yuan(order.amount_cent)}"]
    if order.bonus_cent:
        parts.append(f"赠送 {_yuan(order.bonus_cent)}")
    if previous in ("expired", "failed"):
        parts.append(f"迟到的支付（原状态 {previous}）")
    if note:
        parts.append(note)
    return " · ".join(parts)
