import json
import urllib.parse

from conftest import PASSWORD_HASH
from sqlalchemy import select
from test_payment_orders import sign_with
from test_payment_selfcheck import StubResponse, install, signed_node_response
from test_recharge_config import create_channel, create_package

from app.api.routes import payments
from app.core.security import PORTAL_AUDIENCE, create_access_token
from app.models import Account, BalanceTransaction, PaymentOrder, PaymentOrderEvent
from app.services.payments.providers import alipay, wechat

GATEWAY = "https://openapi.alipay.com/gateway.do"
APIV3_KEY = "KnowforgeTestApiV3Key32Chars!!!!"
FORM_HEADERS = {"content-type": "application/x-www-form-urlencoded"}


def wechat_credentials(payment_keys) -> dict[str, str]:
    """微信侧"公钥模式"最少要这一组；平台证书模式由自检那批用例覆盖。"""
    return {
        "mch_id": "1600000000",
        "app_id": "wx0123456789abcdef",
        "apiv3_key": APIV3_KEY,
        "merchant_private_key": payment_keys["private_pem"],
        "cert_serial_no": "000000001234ABCD",
        "wechat_pay_public_key": payment_keys["public_pem"],
        "wechat_pay_public_key_id": "PUB_KEY_ID_KF0001AB",
        "notify_url": "https://pay.example.test/v1/payments/notify/wx",
    }


async def order_row(context, reference: str) -> PaymentOrder | None:
    async with context["sessions"]() as session:
        return await session.scalar(select(PaymentOrder).where(PaymentOrder.out_trade_no == reference))


async def ledger_rows(context) -> list[BalanceTransaction]:
    async with context["sessions"]() as session:
        return list(await session.scalars(select(BalanceTransaction)))


async def event_rows(context, order_id: int) -> list[PaymentOrderEvent]:
    async with context["sessions"]() as session:
        rows = await session.scalars(
            select(PaymentOrderEvent)
            .where(PaymentOrderEvent.order_id == order_id)
            .order_by(PaymentOrderEvent.id)
        )
        return list(rows)


QR_CODE = "https://qr.alipay.com/kf001"


def precreate_ok(payment_keys, amount: str | None = None):
    """支付宝预下单的正常应答：拿 qr_code，门户据此渲染二维码。

    amount 传了才核对金额，因为折扣单发出去的是折后价，默认不锁死金额才能让各家用例复用。
    """

    def handler(method: str, url: str, data: dict[str, str], headers: dict[str, str]) -> StubResponse:
        assert data["method"] == "alipay.trade.precreate", data.get("method")
        if amount is not None:
            assert json.loads(data["biz_content"])["total_amount"] == amount
        return StubResponse(
            200,
            signed_node_response(
                "alipay_trade_precreate_response",
                {"code": "10000", "msg": "Success", "qr_code": QR_CODE},
                payment_keys["private_pem"],
            ),
        )

    return handler


async def checkout(context, payment_keys, monkeypatch, code="alipay", promo_code=""):
    """备好一个能下单的渠道 + 100 元送 20 元的档位，然后真走一次门户下单。"""
    package = await create_package(context, amount_cent=10000, bonus_cent=2000)
    channel = await create_channel(context, payment_keys, code=code)
    assert package.status_code == 200 and channel.status_code == 200, package.text + channel.text
    install(monkeypatch, alipay, precreate_ok(payment_keys))
    return await context["client"].post(
        "/v1/portal/orders",
        headers=context["customer_headers"],
        json={"channel_code": code, "package_id": package.json()["data"]["id"], "promo_code": promo_code},
    )


async def balance(context) -> int:
    wallet = await context["client"].get("/v1/portal/wallet", headers=context["customer_headers"])
    return wallet.json()["data"]["balance_cent"]


def alipay_notify(payment_keys: dict[str, str], out_trade_no: str, amount: str = "100.00") -> str:
    form = {
        "app_id": "2021000000000000",
        "out_trade_no": out_trade_no,
        "trade_no": "2026092722001000000099",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": amount,
        "gmt_payment": "2026-09-27 12:05:00",
        "sign_type": "RSA2",
        "charset": "utf-8",
    }
    form["sign"] = sign_with(payment_keys["private_pem"], alipay.notify_sign_content(form))
    return urllib.parse.urlencode(form)


async def test_recent_orders_are_listed_for_their_owner_only(context, payment_keys, monkeypatch):
    """没付完的单要能回来继续付：刷新页面后找不回订单，等于把用户的钱挂在空中。"""
    created = await checkout(context, payment_keys, monkeypatch)
    reference = created.json()["data"]["out_trade_no"]
    listed = await context["client"].get("/v1/portal/orders", headers=context["customer_headers"])
    assert listed.status_code == 200
    items = listed.json()["data"]["items"]
    assert [row["out_trade_no"] for row in items] == [reference]
    assert items[0]["channel_name"] == "支付宝" and items[0]["status"] == "pending"

    async with context["sessions"]() as session:
        nosy_account = Account(username="nosy2", password_hash=PASSWORD_HASH, role="end_user")
        session.add(nosy_account)
        await session.commit()
        token = create_access_token(nosy_account.id, context["settings"], PORTAL_AUDIENCE)
    elsewhere = await context["client"].get("/v1/portal/orders", headers={"Authorization": f"Bearer {token}"})
    assert elsewhere.json()["data"]["items"] == []


async def test_checkout_returns_a_qr_code_and_freezes_the_amount_snapshot(context, payment_keys, monkeypatch):
    """下单成功只是"能去扫了"：钱没到，订单必须停在 pending，账本一行都不能有。"""
    package = await create_package(context, amount_cent=10000, bonus_cent=2000)
    await create_channel(context, payment_keys)
    stub = install(monkeypatch, alipay, precreate_ok(payment_keys))
    response = await context["client"].post(
        "/v1/portal/orders",
        headers=context["customer_headers"],
        json={"channel_code": "alipay", "package_id": package.json()["data"]["id"]},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["out_trade_no"].startswith("KF") and data["status"] == "pending"
    assert data["amount_cent"] == 10000 and data["bonus_cent"] == 2000
    assert data["payable_cent"] == 10000 and data["discount_cent"] == 0
    assert data["credited_cent"] == 0
    assert data["code_url"] == QR_CODE and data["redirect_url"] is None
    sent = stub.calls[0]["data"]
    assert json.loads(sent["biz_content"])["out_trade_no"] == data["out_trade_no"]
    # 渠道里配了回调地址就用它；站点基址只是兜底。两者都没有时支付宝只能靠主动查单入账。
    assert sent["notify_url"] == "https://pay.example.test/v1/payments/notify/alipay"
    assert await balance(context) == 0
    assert await ledger_rows(context) == []
    stored = await order_row(context, data["out_trade_no"])
    assert stored.status == "pending" and stored.package_id is not None
    assert [item.kind for item in await event_rows(context, stored.id)] == ["order_created", "order_placed"]


async def test_order_body_cannot_smuggle_its_own_amount(context, payment_keys, monkeypatch):
    package = await create_package(context, amount_cent=10000)
    await create_channel(context, payment_keys)
    response = await context["client"].post(
        "/v1/portal/orders",
        headers=context["customer_headers"],
        json={"channel_code": "alipay", "package_id": package.json()["data"]["id"], "amount_cent": 1},
    )
    assert response.status_code == 400, "客户端报的金额必须被拒，而不是被采纳"


async def test_offline_and_disabled_channels_cannot_take_online_orders(context, payment_keys):
    package = await create_package(context, amount_cent=10000)
    offline = await create_channel(
        context, payment_keys, code="bank", channel_type="custom", credentials={}, enabled=True
    )
    assert offline.status_code == 200, offline.text
    wallet = await context["client"].get("/v1/portal/wallet", headers=context["customer_headers"])
    listed = {row["code"]: row for row in wallet.json()["data"]["channels"]}
    assert listed["bank"]["orderable"] is False and listed["bank"]["channel_type"] == "custom"
    refused = await context["client"].post(
        "/v1/portal/orders",
        headers=context["customer_headers"],
        json={"channel_code": "bank", "package_id": package.json()["data"]["id"]},
    )
    assert refused.status_code == 409 and "不支持" in refused.json()["message"]

    dormant = await create_channel(context, payment_keys, code="alipay-off", enabled=False)
    assert dormant.status_code == 200, dormant.text
    response = await context["client"].post(
        "/v1/portal/orders",
        headers=context["customer_headers"],
        json={"channel_code": "alipay-off", "package_id": package.json()["data"]["id"]},
    )
    assert response.status_code == 409 and "尚未开通" in response.json()["message"]


async def test_a_provider_rejection_is_reported_and_the_order_is_closed(context, payment_keys, monkeypatch):
    """厂商拒单要带上厂商的原因，并且把这单关掉：留一张 pending 的空单等于骗用户再去付。"""
    package = await create_package(context, amount_cent=10000, label="拒单档")
    created = await create_channel(
        context,
        payment_keys,
        code="wx",
        channel_type="wechat",
        credentials=wechat_credentials(payment_keys),
        enabled=True,
    )
    assert created.status_code == 200, created.text
    stub = install(
        monkeypatch, wechat, lambda *a: StubResponse(400, '{"code":"INVALID_REQUEST","message":"额度不足"}')
    )
    response = await context["client"].post(
        "/v1/portal/orders",
        headers=context["customer_headers"],
        json={"channel_code": "wx", "package_id": package.json()["data"]["id"]},
    )
    assert response.status_code == 502, response.text
    assert "INVALID_REQUEST" in response.json()["message"]
    assert stub.calls[0]["url"] == "https://api.mch.weixin.qq.com/v3/pay/transactions/native"

    async with context["sessions"]() as session:
        failed = list(await session.scalars(select(PaymentOrder)))
    assert len(failed) == 1 and failed[0].status == "failed" and failed[0].closed_at is not None
    events = await event_rows(context, failed[0].id)
    assert [item.kind for item in events] == ["order_created", "provider_rejected"]
    assert "INVALID_REQUEST" in events[1].detail["detail"]
    assert await ledger_rows(context) == []


async def test_orders_are_invisible_to_anyone_but_their_owner(context, payment_keys, monkeypatch):
    response = await checkout(context, payment_keys, monkeypatch)
    reference = response.json()["data"]["out_trade_no"]
    async with context["sessions"]() as session:
        nosy_account = Account(username="nosy", password_hash=PASSWORD_HASH, role="end_user")
        session.add(nosy_account)
        await session.commit()
        token = create_access_token(nosy_account.id, context["settings"], PORTAL_AUDIENCE)
    nosy = {"Authorization": f"Bearer {token}"}
    assert (
        await context["client"].get(f"/v1/portal/orders/{reference}", headers=context["customer_headers"])
    ).status_code == 200  # noqa: E501
    stolen = await context["client"].get(f"/v1/portal/orders/{reference}", headers=nosy)
    # 单号可枚举时不能泄露"这单确实有，只是不归你"，所以一律 404。
    assert stolen.status_code == 404
    assert (await context["client"].get("/v1/portal/orders/KF-NOT-REAL", headers=nosy)).status_code == 404


async def test_a_pile_of_unpaid_orders_blocks_new_ones(context, payment_keys, monkeypatch):
    package = await create_package(context, amount_cent=10000, label="刷屏档")
    await create_channel(context, payment_keys, code="spam")
    install(monkeypatch, alipay, precreate_ok(payment_keys))
    for _ in range(payments.MAX_OPEN_ORDERS):
        created = await context["client"].post(
            "/v1/portal/orders",
            headers=context["customer_headers"],
            json={"channel_code": "spam", "package_id": package.json()["data"]["id"]},
        )
        assert created.status_code == 200, created.text
    blocked = await context["client"].post(
        "/v1/portal/orders",
        headers=context["customer_headers"],
        json={"channel_code": "spam", "package_id": package.json()["data"]["id"]},
    )
    assert blocked.status_code == 429 and "上限" in blocked.json()["message"]


async def test_notify_credits_once_and_a_replay_is_inert(context, payment_keys, monkeypatch):
    created = await checkout(context, payment_keys, monkeypatch)
    reference = created.json()["data"]["out_trade_no"]
    body = alipay_notify(payment_keys, reference)
    response = await context["client"].post("/v1/payments/notify/alipay", content=body, headers=FORM_HEADERS)
    # 支付宝认的 ack 就是纯文本 success，回 JSON 会被判成失败然后一直重投。
    assert response.status_code == 200 and response.text == "success"
    assert await balance(context) == 12000

    again = await context["client"].post("/v1/payments/notify/alipay", content=body, headers=FORM_HEADERS)
    assert again.status_code == 200
    assert await balance(context) == 12000
    entries = await ledger_rows(context)
    assert len(entries) == 1 and entries[0].channel == "alipay"
    assert "100.00元" in entries[0].note and "20.00元" in entries[0].note
    stored = await order_row(context, reference)
    assert (
        stored.status == "paid"
        and stored.notify_digest
        and stored.provider_trade_no == "2026092722001000000099"
    )
    assert [item.kind for item in await event_rows(context, stored.id)] == [
        "order_created",
        "order_placed",
        "credited",
        "duplicate_notify",
    ]


async def test_a_notify_with_a_bad_signature_credits_nothing_and_leaves_no_trace(
    context, payment_keys, monkeypatch
):
    created = await checkout(context, payment_keys, monkeypatch)
    reference = created.json()["data"]["out_trade_no"]
    forged = urllib.parse.urlencode(
        {"out_trade_no": reference, "trade_status": "TRADE_SUCCESS", "total_amount": "100.00", "sign": "junk"}
    )
    response = await context["client"].post(
        "/v1/payments/notify/alipay", content=forged, headers=FORM_HEADERS
    )
    assert response.status_code == 400
    assert await balance(context) == 0
    stored = await order_row(context, reference)
    assert stored.status == "pending" and stored.notify_digest is None
    # 验签都没过的报文，它声称的单号可信度为零，不该在订单时间线上留下任何东西。
    assert [item.kind for item in await event_rows(context, stored.id)] == ["order_created", "order_placed"]


async def test_a_notify_that_is_two_days_late_on_amount_is_held_not_credited(
    context, payment_keys, monkeypatch
):
    created = await checkout(context, payment_keys, monkeypatch)
    reference = created.json()["data"]["out_trade_no"]
    body = alipay_notify(payment_keys, reference, amount="0.01")
    response = await context["client"].post("/v1/payments/notify/alipay", content=body, headers=FORM_HEADERS)
    assert response.status_code == 200, "重投不会让金额变对，停掉厂商重试、留给人查"
    assert await balance(context) == 0
    stored = await order_row(context, reference)
    assert stored.status == "pending"
    assert [item.kind for item in await event_rows(context, stored.id)][-1] == "amount_mismatch"


async def test_a_notify_for_an_unknown_channel_is_refused(context, payment_keys, monkeypatch):
    created = await checkout(context, payment_keys, monkeypatch)
    body = alipay_notify(payment_keys, created.json()["data"]["out_trade_no"])
    unknown = await context["client"].post(
        "/v1/payments/notify/never-heard", content=body, headers=FORM_HEADERS
    )
    assert unknown.status_code == 400
    assert await balance(context) == 0


async def test_sync_settles_an_order_when_the_provider_says_paid(context, payment_keys, monkeypatch):
    """本地拿不到 https 公网回调，主动查单是这条链路现在唯一能真正常入账的路径。"""
    created = await checkout(context, payment_keys, monkeypatch)
    reference = created.json()["data"]["out_trade_no"]

    def handler(method: str, url: str, data: dict[str, str], headers: dict[str, str]) -> StubResponse:
        assert data["method"] == "alipay.trade.query"
        assert json.loads(data["biz_content"])["out_trade_no"] == reference
        return StubResponse(
            200,
            signed_node_response(
                "alipay_trade_query_response",
                {
                    "code": "10000",
                    "msg": "Success",
                    "out_trade_no": reference,
                    "trade_no": "2026092722001000000077",
                    "trade_status": "TRADE_SUCCESS",
                    "buyer_pay_amount": "100.00",
                },
                payment_keys["private_pem"],
            ),
        )

    stub = install(monkeypatch, alipay, handler)
    response = await context["client"].post(
        f"/v1/portal/orders/{reference}/sync", headers=context["customer_headers"]
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "paid" and data["credited_cent"] == 12000
    assert data["sync"] == {"ok": True, "detail": "已入账"}
    assert data["balance_cent"] == 12000
    # 已付的订单不必再回传付款地址，留着只会被误点。
    assert data["code_url"] is None and data["redirect_url"] is None
    assert stub.calls[0]["url"] == GATEWAY
    assert len(await ledger_rows(context)) == 1

    second = await context["client"].post(
        f"/v1/portal/orders/{reference}/sync", headers=context["customer_headers"]
    )
    assert second.json()["data"]["status"] == "paid"
    assert len(await ledger_rows(context)) == 1, "重复查单不能重复入账"
    stored = await order_row(context, reference)
    assert stored.attempts == 1, "查单计数只在真打过厂商时增长"
    assert [item.kind for item in await event_rows(context, stored.id)] == [
        "order_created",
        "order_placed",
        "credited",
    ]


async def test_sync_reports_an_unpaid_order_without_inventing_a_credit(context, payment_keys, monkeypatch):
    created = await checkout(context, payment_keys, monkeypatch)
    reference = created.json()["data"]["out_trade_no"]
    install(
        monkeypatch,
        alipay,
        lambda *a: StubResponse(
            200,
            signed_node_response(
                "alipay_trade_query_response",
                {
                    "code": "10000",
                    "msg": "Success",
                    "out_trade_no": reference,
                    "trade_status": "WAIT_BUYER_PAY",
                },
                payment_keys["private_pem"],
            ),
        ),
    )
    response = await context["client"].post(
        f"/v1/portal/orders/{reference}/sync", headers=context["customer_headers"]
    )
    data = response.json()["data"]
    assert data["status"] == "pending" and data["sync"]["ok"] is False
    assert "WAIT_BUYER_PAY" in data["sync"]["detail"]
    assert data["balance_cent"] == 0


async def test_sync_survives_a_provider_outage_as_a_reported_failure(context, payment_keys, monkeypatch):
    created = await checkout(context, payment_keys, monkeypatch)
    reference = created.json()["data"]["out_trade_no"]
    install(monkeypatch, alipay, lambda *a: StubResponse(500, "gateway down"))
    response = await context["client"].post(
        f"/v1/portal/orders/{reference}/sync", headers=context["customer_headers"]
    )
    assert response.status_code == 200, "厂商临时不可用不该让用户看到 5xx"
    data = response.json()["data"]
    assert data["sync"]["ok"] is False and data["status"] == "pending"
    assert await balance(context) == 0


def test_notify_ack_text_is_what_each_provider_expects():
    """回错文本厂商会一直重投：支付宝要纯文本 success，微信要 200 + JSON。"""
    assert payments._ack("alipay").body == b"success"
    assert payments._ack("wechat").status_code == 200
    assert payments._ack("stripe").status_code == 200
    assert payments._reject("alipay", "x").status_code == 400
    assert payments._reject("wechat", "x").status_code == 400


def test_out_trade_no_is_unique_enough_and_fits_the_column():
    generated = {payments.out_trade_no() for _ in range(500)}
    assert len(generated) == 500
    assert all(len(item) <= 64 and item.startswith("KF") for item in generated)
