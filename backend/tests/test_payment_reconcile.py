from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from test_payment_checkout import GATEWAY
from test_payment_selfcheck import StubResponse, install, signed_node_response
from test_recharge_config import create_channel, create_package

from app.models import BalanceTransaction, PaymentOrder, PaymentOrderEvent
from app.services import billing
from app.services.payments import reconcile, settlement
from app.services.payments.providers import alipay

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


async def make_channel(context, payment_keys, code: str = "alipay") -> int:
    created = await create_channel(context, payment_keys, code=code)
    assert created.status_code == 200, created.text
    return created.json()["data"]["id"]


async def make_order(
    context,
    channel_id: int,
    reference: str,
    status: str = "pending",
    expires_at: datetime = NOW - timedelta(minutes=1),
    **extra: Any,
) -> int:
    async with context["sessions"]() as session:
        order = PaymentOrder(
            out_trade_no=reference,
            account_id=context["customer"].id,
            channel_id=channel_id,
            amount_cent=10000,
            bonus_cent=2000,
            status=status,
            expires_at=expires_at,
            **extra,
        )
        session.add(order)
        await session.commit()
        return order.id


async def run_sweep(context, *, now=NOW, limit=50) -> dict[str, int]:
    async with context["sessions"]() as session:
        result = await reconcile.close_due_orders(
            session, context["settings"].payment_master_key_bytes, now=now, limit=limit
        )
        await session.commit()
        return result


async def load_order(context, order_id: int) -> PaymentOrder:
    async with context["sessions"]() as session:
        return await session.get(PaymentOrder, order_id)


def trade_status(payment_keys, reference: str, status: str, amount: str = "100.00") -> StubResponse:
    return StubResponse(
        200,
        signed_node_response(
            "alipay_trade_query_response",
            {
                "code": "10000",
                "msg": "Success",
                "out_trade_no": reference,
                "trade_no": "2026092722001000000123",
                "trade_status": status,
                "buyer_pay_amount": amount,
            },
            payment_keys["private_pem"],
        ),
    )


async def test_a_due_order_is_only_closed_after_the_provider_confirms_nothing_arrived(
    context, payment_keys, monkeypatch
):
    channel_id = await make_channel(context, payment_keys)
    order_id = await make_order(context, channel_id, "KF-SWEEP-1")
    stub = install(monkeypatch, alipay, lambda *a: trade_status(payment_keys, "KF-SWEEP-1", "WAIT_BUYER_PAY"))
    assert await run_sweep(context) == {"expired": 1, "credited": 0, "unverified": 0, "held": 0}
    assert stub.calls[0]["url"] == GATEWAY
    order = await load_order(context, order_id)
    assert order.status == "expired" and order.closed_at is not None
    event = (await _events(context, order_id))[0]
    assert event.kind == "expired" and event.detail["verified"] is True
    assert await ledger_rows(context) == []


async def test_a_payment_that_landed_after_the_deadline_is_credited_instead_of_closed(
    context, payment_keys, monkeypatch
):
    channel_id = await make_channel(context, payment_keys)
    order_id = await make_order(context, channel_id, "KF-SWEEP-2")
    install(monkeypatch, alipay, lambda *a: trade_status(payment_keys, "KF-SWEEP-2", "TRADE_SUCCESS"))
    assert await run_sweep(context) == {"expired": 0, "credited": 1, "unverified": 0, "held": 0}
    order = await load_order(context, order_id)
    assert order.status == "paid" and order.provider_trade_no == "2026092722001000000123"
    entries = await ledger_rows(context)
    assert [row.amount_cent for row in entries] == [12000]
    assert "对账查单" in entries[0].note
    async with context["sessions"]() as session:
        assert await billing.balance_cent(session, order.account_id) == 12000


async def test_an_unreachable_provider_still_closes_the_order_but_says_so(context, payment_keys, monkeypatch):
    """问不到答案和问到了说没付是两件事：结果都是关单，但计数与事件要能分开。"""
    channel_id = await make_channel(context, payment_keys)
    order_id = await make_order(context, channel_id, "KF-SWEEP-3")

    def boom(*_args: Any) -> StubResponse:
        raise httpx.ConnectError("gateway down")

    install(monkeypatch, alipay, boom)
    assert await run_sweep(context) == {"expired": 0, "credited": 0, "unverified": 1, "held": 0}
    order = await load_order(context, order_id)
    assert order.status == "expired"
    event = (await _events(context, order_id))[0]
    assert event.detail["verified"] is False and event.detail["detail"] == "未向厂商确认"


async def test_a_mismatched_order_is_taken_out_of_the_sweep(context, payment_keys, monkeypatch):
    """金额对不上已经挂给人查了，再每分钟重查一次厂商会把对账变成无限重试。"""
    channel_id = await make_channel(context, payment_keys)
    order_id = await make_order(context, channel_id, "KF-SWEEP-4")
    install(monkeypatch, alipay, lambda *a: trade_status(payment_keys, "KF-SWEEP-4", "TRADE_SUCCESS", "0.01"))
    first = await run_sweep(context)
    assert first["held"] == 1
    order = await load_order(context, order_id)
    assert order.status == "pending", "挂起的单不该被翻成 expired，它等的是人来判"
    stub = install(monkeypatch, alipay, lambda *a: trade_status(payment_keys, "KF-SWEEP-4", "TRADE_SUCCESS"))
    assert await run_sweep(context) == {"expired": 0, "credited": 0, "unverified": 0, "held": 0}
    assert stub.calls == [], "已挂起的单不该再打厂商"
    async with context["sessions"]() as session:
        queue = await reconcile.review_queue(session)
    assert [row.out_trade_no for row in queue] == ["KF-SWEEP-4"]


async def test_orders_that_are_not_due_are_left_alone(context, payment_keys, monkeypatch):
    channel_id = await make_channel(context, payment_keys)
    order_id = await make_order(context, channel_id, "KF-SWEEP-5", expires_at=NOW + timedelta(minutes=20))
    stub = install(monkeypatch, alipay, lambda *a: trade_status(payment_keys, "KF-SWEEP-5", "TRADE_SUCCESS"))
    assert await run_sweep(context) == {"expired": 0, "credited": 0, "unverified": 0, "held": 0}
    assert stub.calls == []
    assert (await load_order(context, order_id)).status == "pending"


async def test_running_the_sweep_twice_credits_once(context, payment_keys, monkeypatch):
    channel_id = await make_channel(context, payment_keys)
    await make_order(context, channel_id, "KF-SWEEP-6")
    install(monkeypatch, alipay, lambda *a: trade_status(payment_keys, "KF-SWEEP-6", "TRADE_SUCCESS"))
    assert (await run_sweep(context))["credited"] == 1
    assert await run_sweep(context) == {"expired": 0, "credited": 0, "unverified": 0, "held": 0}
    assert len(await ledger_rows(context)) == 1


async def test_the_sweep_is_bounded_by_the_batch_size(context, payment_keys, monkeypatch):
    channel_id = await make_channel(context, payment_keys)
    for index in range(4):
        await make_order(context, channel_id, f"KF-BATCH-{index}")
    install(monkeypatch, alipay, lambda *a: trade_status(payment_keys, "KF-BATCH-0", "WAIT_BUYER_PAY"))
    assert sum((await run_sweep(context, limit=2)).values()) == 2
    assert sum((await run_sweep(context, limit=2)).values()) == 2


async def _events(context, order_id: int) -> list[PaymentOrderEvent]:
    async with context["sessions"]() as session:
        rows = await session.scalars(
            select(PaymentOrderEvent)
            .where(PaymentOrderEvent.order_id == order_id)
            .order_by(PaymentOrderEvent.id)
        )
        return list(rows)


async def ledger_rows(context) -> list[BalanceTransaction]:
    async with context["sessions"]() as session:
        return list(await session.scalars(select(BalanceTransaction)))


async def test_admin_can_filter_orders_and_sees_the_account_behind_them(context, payment_keys, monkeypatch):
    await create_package(context, label="对账档", amount_cent=10000, bonus_cent=2000)
    channel_id = await make_channel(context, payment_keys, code="alipay-r")
    await make_order(context, channel_id, "KF-ADMIN-1")
    install(monkeypatch, alipay, lambda *a: trade_status(payment_keys, "KF-ADMIN-1", "TRADE_SUCCESS"))
    await run_sweep(context)

    listed = await context["client"].get("/v1/admin/recharge/orders", headers=context["admin_headers"])
    assert listed.status_code == 200
    item = listed.json()["data"]["items"][0]
    assert item["out_trade_no"] == "KF-ADMIN-1" and item["status"] == "paid"
    assert item["channel_code"] == "alipay-r" and item["account_username"] == "customer"
    assert item["credited_cent"] == 12000 and listed.json()["data"]["total"] == 1

    by_status = await context["client"].get(
        "/v1/admin/recharge/orders?status=pending", headers=context["admin_headers"]
    )
    assert by_status.json()["data"]["items"] == []
    by_channel = await context["client"].get(
        "/v1/admin/recharge/orders?channel_code=other", headers=context["admin_headers"]
    )
    assert by_channel.json()["data"]["total"] == 0
    review = await context["client"].get(
        "/v1/admin/recharge/orders?review=true", headers=context["admin_headers"]
    )
    assert review.json()["data"]["items"] == []
    prefix = await context["client"].get(
        "/v1/admin/recharge/orders?q=KF-ADMIN", headers=context["admin_headers"]
    )
    assert [row["out_trade_no"] for row in prefix.json()["data"]["items"]] == ["KF-ADMIN-1"]

    detail = await context["client"].get(
        "/v1/admin/recharge/orders/KF-ADMIN-1", headers=context["admin_headers"]
    )
    kinds = [entry["kind"] for entry in detail.json()["data"]["events"]]
    assert kinds == ["credited"] and detail.json()["data"]["account_username"] == "customer"
    missing = await context["client"].get("/v1/admin/recharge/orders/nope", headers=context["admin_headers"])
    assert missing.status_code == 404


async def test_the_order_ledger_is_admin_only(context, payment_keys):
    channel_id = await make_channel(context, payment_keys, code="alipay-acl")
    await make_order(context, channel_id, "KF-ADMIN-2")
    for path in ("/v1/admin/recharge/orders", "/v1/admin/recharge/orders/KF-ADMIN-2"):
        response = await context["client"].get(path, headers=context["customer_headers"])
        assert response.status_code in (401, 403), f"{path} 不应对门户账号开放"


async def test_a_mismatch_shows_up_in_the_review_filter(context, payment_keys, monkeypatch):
    channel_id = await make_channel(context, payment_keys, code="alipay-m")
    await make_order(context, channel_id, "KF-ADMIN-3")
    install(monkeypatch, alipay, lambda *a: trade_status(payment_keys, "KF-ADMIN-3", "TRADE_SUCCESS", "0.01"))
    await run_sweep(context)
    listed = await context["client"].get(
        "/v1/admin/recharge/orders?review=true", headers=context["admin_headers"]
    )
    items = listed.json()["data"]["items"]
    assert [row["out_trade_no"] for row in items] == ["KF-ADMIN-3"]
    assert items[0]["status"] == "pending"
    detail = await context["client"].get(
        "/v1/admin/recharge/orders/KF-ADMIN-3", headers=context["admin_headers"]
    )
    mismatch = detail.json()["data"]["events"][0]
    assert mismatch["kind"] == settlement.MISMATCH
    assert mismatch["detail"] == {
        "expected_cent": 10000,
        "received_cent": 1,
        "by": "alipay-m",
    }
