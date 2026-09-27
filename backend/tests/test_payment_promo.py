from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from test_payment_checkout import precreate_ok, wechat_credentials
from test_payment_selfcheck import StubResponse, install, signed_node_response
from test_recharge_config import create_channel, create_package

from app.models import BalanceTransaction, PaymentOrder, RechargePromoCode
from app.services.payments.providers import alipay, wechat

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


async def make_promo(context, code="KFNEW100", **extra: Any) -> int:
    body = {
        "code": code,
        "label": "新用户立减",
        "kind": "amount_off",
        "value": 1000,
        "min_amount_cent": 0,
        "starts_at": None,
        "ends_at": None,
        "max_uses": None,
        "per_account_limit": None,
        "enabled": True,
        **extra,
    }
    response = await context["client"].post(
        "/v1/admin/recharge/promo-codes", headers=context["admin_headers"], json=body
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


async def quote(context, package_id: int, code: str):
    return await context["client"].post(
        "/v1/portal/promo/quote",
        headers=context["customer_headers"],
        json={"package_id": package_id, "promo_code": code},
    )


async def checkout_with(
    context, payment_keys, monkeypatch, package_id: int, code: str, channel="alipay", expect_amount=None
):
    if not any(
        row["code"] == channel
        for row in (
            await context["client"].get("/v1/admin/recharge/channels", headers=context["admin_headers"])
        ).json()["data"]["items"]
    ):
        assert (await create_channel(context, payment_keys, code=channel)).status_code == 200
    install(monkeypatch, alipay, precreate_ok(payment_keys, expect_amount))
    return await context["client"].post(
        "/v1/portal/orders",
        headers=context["customer_headers"],
        json={"channel_code": channel, "package_id": package_id, "promo_code": code},
    )


async def test_a_valid_code_lowers_what_the_user_pays_but_not_what_they_get(
    context, payment_keys, monkeypatch
):
    """促销让掉的是收单的钱，不是给用户的额度：到账金额必须与不打折时一致。"""
    package = await create_package(context, amount_cent=10000, bonus_cent=2000)
    package_id = package.json()["data"]["id"]
    await make_promo(context)
    priced = (await quote(context, package_id, "KFNEW100")).json()["data"]
    assert priced == {
        "applied": True,
        "label": "新用户立减",
        "amount_cent": 10000,
        "bonus_cent": 2000,
        "discount_cent": 1000,
        "payable_cent": 9000,
        "credited_cent": 12000,
        "reason": "",
    }

    response = await checkout_with(context, payment_keys, monkeypatch, package_id, "kfnew100")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["payable_cent"] == 9000 and data["discount_cent"] == 1000
    assert data["credited_cent"] == 0
    async with context["sessions"]() as session:
        order = await session.scalar(select(PaymentOrder))
        assert order.payable_cent == 9000 and order.promo_code_id is not None
        assert order.amount_cent == 10000, "原价快照不能被折扣改写，否则对账没有基准"


async def test_the_provider_is_asked_for_the_discounted_amount(context, payment_keys, monkeypatch):
    package = await create_package(context, amount_cent=10000, bonus_cent=0, label="折扣档")
    await make_promo(context, code="KFPCT10", kind="percent", value=10, label="九折")
    # 金额断言在桩里：发给厂商的 total_amount 必须是折后的 90.00，不是档位原价。
    response = await checkout_with(
        context, payment_keys, monkeypatch, package.json()["data"]["id"], "KFPCT10", expect_amount="90.00"
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["payable_cent"] == 9000


async def test_a_percent_code_rounds_down_to_the_user_s_advantage_but_never_below_one_fen(
    context, payment_keys
):
    package = await create_package(context, amount_cent=999, bonus_cent=0, label="三角九分档")
    await make_promo(context, code="KFPCT50", kind="percent", value=50)
    priced = (await quote(context, package.json()["data"]["id"], "KFPCT50")).json()["data"]
    # 999 * 50 // 100 = 499：向下取整让用户少付，而不是多付。
    assert priced["discount_cent"] == 499 and priced["payable_cent"] == 500


CASES: list[tuple[str, dict[str, Any], str]] = [
    ("KFNONE", {}, "促销码不存在"),
    ("KFOFF", {"enabled": False}, "促销码已停用"),
    ("KFFUTURE", {"starts_at": (NOW + timedelta(days=1)).isoformat()}, "促销码尚未生效"),
    ("KFEXPIRED", {"ends_at": (NOW - timedelta(days=1)).isoformat()}, "促销码已过期"),
    ("KFMIN", {"min_amount_cent": 50000}, "该码需单笔满 500.00元"),
    ("KFBIG", {"value": 10000}, "本单金额不足以使用该促销码"),
]


async def test_every_rejected_code_says_why(context, payment_keys):
    package = await create_package(context, amount_cent=10000, bonus_cent=0, label="校验档")
    package_id = package.json()["data"]["id"]
    for code, extra, expected in CASES:
        if code != "KFNONE":
            await make_promo(context, code=code, **extra)
        body = (await quote(context, package_id, code)).json()["data"]
        assert body["applied"] is False and body["discount_cent"] == 0, code
        assert body["reason"] == expected, (code, body["reason"])
        assert body["payable_cent"] == 10000, "被拒的码不能让金额发生变化"

    # 名额用完要先把计数推上去：used_count 只在订单真的入账时才增长，下单不占额度。
    await make_promo(context, code="KFFULL", max_uses=1)
    async with context["sessions"]() as session:
        row = await session.scalar(select(RechargePromoCode).where(RechargePromoCode.code == "KFFULL"))
        row.used_count = 1
        await session.commit()
    full = (await quote(context, package_id, "KFFULL")).json()["data"]
    assert full["reason"] == "促销码名额已用完"


async def test_an_empty_code_is_a_plain_full_price_quote(context, payment_keys):
    package = await create_package(context, amount_cent=10000, bonus_cent=2000, label="原价档")
    body = (await quote(context, package.json()["data"]["id"], "   ")).json()["data"]
    assert body["applied"] is False and body["reason"] == "" and body["payable_cent"] == 10000


async def test_a_rejected_code_blocks_the_order_instead_of_silently_charging_full(
    context, payment_keys, monkeypatch
):
    await make_promo(context, code="KFMIN", min_amount_cent=50000)
    package = await create_package(context, amount_cent=10000, bonus_cent=0, label="不够档")
    response = await checkout_with(context, payment_keys, monkeypatch, package.json()["data"]["id"], "KFMIN")
    assert response.status_code == 400
    assert "满 500.00元" in response.json()["message"]
    async with context["sessions"]() as session:
        assert (await session.scalars(select(PaymentOrder))).all() == []


async def test_the_per_account_limit_only_counts_orders_that_actually_paid(
    context, payment_keys, monkeypatch
):
    package = await create_package(context, amount_cent=10000, bonus_cent=0, label="限用档")
    package_id = package.json()["data"]["id"]
    await make_promo(context, code="KFONCE", per_account_limit=1)
    first = await checkout_with(context, payment_keys, monkeypatch, package_id, "KFONCE")
    assert first.status_code == 200, first.text
    # 没付的第二单：额度还没被占用，但同一账号也不能挂两张用同一码的单去刷价格。
    second = await checkout_with(context, payment_keys, monkeypatch, package_id, "KFONCE")
    assert second.status_code == 200, "占用发生在入账时，不在下单时"

    reference = first.json()["data"]["out_trade_no"]
    install(
        monkeypatch,
        alipay,
        lambda *a: StubResponse(
            200,
            signed_node_response(
                "alipay_trade_query_response",
                {
                    "code": "10000",
                    "out_trade_no": reference,
                    "trade_status": "TRADE_SUCCESS",
                    "buyer_pay_amount": "90.00",
                },
                payment_keys["private_pem"],
            ),
        ),
    )
    synced = await context["client"].post(
        f"/v1/portal/orders/{reference}/sync", headers=context["customer_headers"]
    )
    assert synced.json()["data"]["status"] == "paid"
    assert synced.json()["data"]["credited_cent"] == 10000, "折后付款仍按原价入账"
    async with context["sessions"]() as session:
        entry = (await session.scalars(select(BalanceTransaction))).all()
        assert len(entry) == 1 and entry[0].amount_cent == 10000
        assert "促销抵扣 10.00元" in entry[0].note
        promo = await session.scalar(select(RechargePromoCode))
        assert promo.used_count == 1
    blocked = await quote(context, package_id, "KFONCE")
    assert blocked.json()["data"]["reason"] == "你已经用过这个促销码"


async def test_admin_promo_crud_normalises_the_code_and_protects_referenced_rows(context):
    created = await context["client"].post(
        "/v1/admin/recharge/promo-codes",
        headers=context["admin_headers"],
        json={"code": "kf_lower", "label": "", "kind": "amount_off", "value": 500},
    )
    assert created.status_code == 200, created.text
    promo_id = created.json()["data"]["id"]
    assert created.json()["data"]["code"] == "KF_LOWER", "大小写不该变成两个不同的码"
    assert created.json()["data"]["label"] == ""

    duplicate = await context["client"].post(
        "/v1/admin/recharge/promo-codes",
        headers=context["admin_headers"],
        json={"code": "kf_lower", "label": "", "kind": "amount_off", "value": 500},
    )
    assert duplicate.status_code == 409 and "已存在" in duplicate.json()["message"]

    patched = await context["client"].patch(
        f"/v1/admin/recharge/promo-codes/{promo_id}",
        headers=context["admin_headers"],
        json={"enabled": False},
    )
    assert patched.json()["data"]["enabled"] is False and patched.json()["data"]["code"] == "KF_LOWER"

    listed = await context["client"].get("/v1/admin/recharge/promo-codes", headers=context["admin_headers"])
    assert [row["code"] for row in listed.json()["data"]["items"]] == ["KF_LOWER"]

    deleted = await context["client"].delete(
        f"/v1/admin/recharge/promo-codes/{promo_id}", headers=context["admin_headers"]
    )
    assert deleted.status_code == 200

    invalid = await context["client"].post(
        "/v1/admin/recharge/promo-codes",
        headers=context["admin_headers"],
        json={"code": "KFBAD", "label": "", "kind": "percent", "value": 95},
    )
    assert invalid.status_code == 400, "折扣上限 90 由 schema 挡住，不能让活动变成免费"


async def test_a_promo_used_by_an_order_cannot_be_deleted(context, payment_keys, monkeypatch):
    package = await create_package(context, amount_cent=10000, bonus_cent=0, label="留痕档")
    promo_id = await make_promo(context, code="KFKEEP")
    await checkout_with(context, payment_keys, monkeypatch, package.json()["data"]["id"], "KFKEEP")
    deleted = await context["client"].delete(
        f"/v1/admin/recharge/promo-codes/{promo_id}", headers=context["admin_headers"]
    )
    assert deleted.status_code == 409 and "只能停用" in deleted.json()["message"]


async def test_promo_endpoints_follow_the_same_auth_split_as_the_rest(context, payment_keys):
    await make_promo(context, code="KFAUTH")
    anonymous = await context["client"].post(
        "/v1/portal/promo/quote", json={"package_id": 1, "promo_code": "x"}
    )
    assert anonymous.status_code in (401, 403)
    portal_tries_admin = await context["client"].get(
        "/v1/admin/recharge/promo-codes", headers=context["customer_headers"]
    )
    assert portal_tries_admin.status_code in (401, 403)


async def test_a_wechat_order_carries_the_discount_too(context, payment_keys, monkeypatch):
    """折扣是订单层面的事实，不该只有某一家厂商认得它。"""
    package = await create_package(context, amount_cent=10000, bonus_cent=0, label="微信档")
    await make_promo(context, code="KFWX", kind="percent", value=25)
    created = await create_channel(
        context,
        payment_keys,
        code="wxpromo",
        channel_type="wechat",
        credentials=wechat_credentials(payment_keys),
        enabled=True,
    )
    assert created.status_code == 200, created.text
    stub = install(
        monkeypatch,
        wechat,
        lambda *a: StubResponse(200, '{"code_url":"weixin://wxpay/bizpayurl?pr=PROMO"}'),
    )
    response = await context["client"].post(
        "/v1/portal/orders",
        headers=context["customer_headers"],
        json={"channel_code": "wxpromo", "package_id": package.json()["data"]["id"], "promo_code": "KFWX"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["payable_cent"] == 7500
    assert stub.calls[0]["data"]["amount"]["total"] == 7500
    assert response.json()["data"]["code_url"] == "weixin://wxpay/bizpayurl?pr=PROMO"
