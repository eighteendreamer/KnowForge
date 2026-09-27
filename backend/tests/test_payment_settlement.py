from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models import BalanceTransaction, PaymentOrder, PaymentOrderEvent, RechargeChannel, RechargePackage
from app.services import billing
from app.services.payments import settlement

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def alipay_channel(context):
    """渠道和档位要先存在才能挂订单；context 每个用例回滚外层事务，所以不用手工清表。"""
    async with context["sessions"]() as session:
        channel = RechargeChannel(
            code="alipay-cn", display_name="支付宝", channel_type="alipay", enabled=True
        )
        package = RechargePackage(label="标准档", amount_cent=10000, bonus_cent=2000)
        session.add_all([channel, package])
        await session.commit()
        return {"channel_id": channel.id, "code": channel.code, "package_id": package.id}


async def seed_order(alipay_channel, context, status: str = "pending", **overrides: Any) -> PaymentOrder:
    async with context["sessions"]() as session:
        order = PaymentOrder(
            out_trade_no=overrides.pop("out_trade_no", "KF-SEED-1"),
            account_id=context["customer"].id,
            channel_id=alipay_channel["channel_id"],
            package_id=alipay_channel["package_id"],
            amount_cent=10000,
            bonus_cent=2000,
            payable_cent=10000,
            status=status,
            expires_at=NOW + timedelta(minutes=30),
            **overrides,
        )
        session.add(order)
        await session.commit()
        return order


async def ledger_of(session, order_id: int) -> list[BalanceTransaction]:
    return list(
        await session.scalars(
            select(BalanceTransaction).where(BalanceTransaction.payment_order_id == order_id)
        )
    )


async def kinds_of(session, order_id: int) -> list[str]:
    rows = await session.scalars(
        select(PaymentOrderEvent.kind)
        .where(PaymentOrderEvent.order_id == order_id)
        .order_by(PaymentOrderEvent.id)
    )
    return list(rows)


async def test_settlement_credits_once_and_repeats_are_inert(context, alipay_channel):
    """回调会重投、查单会竞速：抢到 paid 跃迁的那一次才允许动账本。"""
    order = await seed_order(alipay_channel, context, out_trade_no="KF-SETTLE-1")
    async with context["sessions"]() as session:
        row = await session.get(PaymentOrder, order.id)
        first = await settlement.mark_paid_and_credit(
            session, row, source="alipay-cn", received_cent=10000, provider_trade_no="2026T0001"
        )
        assert first.ok and first.credited_cent == 12000
        # detail 走的是"原状态是 created/pending"这一支，能等值说明读回来的确实是状态字符串。
        assert first.detail == "已入账"
        await session.commit()

    async with context["sessions"]() as session:
        row = await session.get(PaymentOrder, order.id)
        second = await settlement.mark_paid_and_credit(
            session, row, source="alipay-cn", received_cent=10000, provider_trade_no="2026T0001"
        )
        assert second.outcome == settlement.DUPLICATE and second.credited_cent == 0
        assert second.status == "paid"

        entries = await ledger_of(session, order.id)
        assert len(entries) == 1, "同一张订单只能有一行流水，多一行就是账本被写脏了"
        assert entries[0].amount_cent == 12000
        assert entries[0].channel == "alipay-cn"
        assert entries[0].operator_id is None
        assert "100.00元" in entries[0].note and "20.00元" in entries[0].note
        assert await billing.balance_cent(session, order.account_id) == 12000
        assert await kinds_of(session, order.id) == ["credited", "duplicate_notify"]
        await session.commit()


async def test_settlement_refuses_to_credit_a_mismatched_amount(context, alipay_channel):
    """厂商回执金额和订单对不上时一分钱都不能记：宁可挂起等人工，也不要静默资损。"""
    order = await seed_order(alipay_channel, context, out_trade_no="KF-SETTLE-2")
    async with context["sessions"]() as session:
        row = await session.get(PaymentOrder, order.id)
        result = await settlement.mark_paid_and_credit(session, row, source="alipay-cn", received_cent=1)
        assert result.outcome == settlement.MISMATCH and not result.ok
        assert result.credited_cent == 0
        assert row.status == "pending"
        assert await ledger_of(session, order.id) == []
        assert await billing.balance_cent(session, order.account_id) == 0
        assert await kinds_of(session, order.id) == [settlement.MISMATCH]
        await session.commit()

    # 金额澄清之后仍然可以正常入账，挂起不是把订单永久冻住。
    async with context["sessions"]() as session:
        row = await session.get(PaymentOrder, order.id)
        settled = await settlement.mark_paid_and_credit(session, row, source="manual", received_cent=10000)
        assert settled.ok
        await session.commit()


async def test_a_payment_that_lands_after_the_order_expired_is_still_credited(context, alipay_channel):
    order = await seed_order(alipay_channel, context, status="expired", out_trade_no="KF-SETTLE-3")
    async with context["sessions"]() as session:
        row = await session.get(PaymentOrder, order.id)
        result = await settlement.mark_paid_and_credit(session, row, source="wechat-cn", received_cent=10000)
        assert result.ok and "先前已关闭" in result.detail
        entries = await ledger_of(session, order.id)
        assert "迟到的支付" in entries[0].note and "expired" in entries[0].note
        await session.commit()


async def test_settlement_fills_missing_provider_fields_without_clobbering_them(context, alipay_channel):
    order = await seed_order(
        alipay_channel, context, out_trade_no="KF-SETTLE-4", provider_trade_no="FIRST", paid_at=NOW
    )
    async with context["sessions"]() as session:
        row = await session.get(PaymentOrder, order.id)
        await settlement.mark_paid_and_credit(
            session, row, source="alipay-cn", notify_digest="d1", provider_trade_no=None
        )
        await session.refresh(row)
        assert row.provider_trade_no == "FIRST" and row.notify_digest == "d1" and row.paid_at == NOW
        # 抢不到跃迁时不许顺手改厂商交易号或支付时间，否则事后对账会被覆盖掉。
        await settlement.mark_paid_and_credit(
            session, row, source="query", provider_trade_no="SECOND", notify_digest="d2"
        )
        await session.refresh(row)
        assert row.provider_trade_no == "FIRST" and row.notify_digest == "d1" and row.paid_at == NOW
        await session.commit()


async def test_find_by_out_trade_no_ignores_a_blank_number(context, alipay_channel):
    order = await seed_order(alipay_channel, context, out_trade_no="KF-SETTLE-5")
    async with context["sessions"]() as session:
        assert await settlement.find_by_out_trade_no(session, "") is None
        found = await settlement.find_by_out_trade_no(session, order.out_trade_no)
        assert found is not None and found.id == order.id
        assert await settlement.find_by_out_trade_no(session, "KF-NOPE") is None


async def test_manual_recharge_still_leaves_the_order_link_empty(context, alipay_channel):
    """人工入账与在线入账必须在账本上分得开，否则审计查不清"这笔钱是谁确认收到的"。"""
    async with context["sessions"]() as session:
        account_id = context["admin"].id
        session.add(BalanceTransaction(account_id=account_id, amount_cent=500, channel="manual"))
        await session.flush()
        linked = await session.scalar(
            select(func.count())
            .select_from(BalanceTransaction)
            .where(BalanceTransaction.account_id == account_id, BalanceTransaction.payment_order_id.is_(None))
        )
        assert linked == 1
        await session.rollback()
