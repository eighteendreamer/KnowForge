import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from test_payment_selfcheck import (
    APIV3_KEY,
    StubResponse,
    encrypt_certificate,
    install,
    signed_node_response,
)
from test_recharge_config import alipay_credentials

from app.services.payments.providers import alipay, registry, stripe, wechat
from app.services.payments.providers.base import NotifyRequest, OrderRequest, ProviderError, yuan_to_cent

UNSIGNED = '{"alipay_trade_query_response":{"code":"10000","trade_status":"TRADE_SUCCESS"}}'
ORDER = OrderRequest(
    out_trade_no="KF20260927A0001",
    amount_cent=10000,
    subject="KnowForge 额度充值",
    notify_url="https://pay.example.test/v1/payments/notify/x",
    return_url="https://portal.example.test/recharge",
)


def wechat_credentials(payment_keys) -> dict[str, str]:
    return {
        "mch_id": "1600000000",
        "app_id": "wx0123456789abcdef",
        "apiv3_key": APIV3_KEY,
        "merchant_private_key": payment_keys["private_pem"],
        "cert_serial_no": "000000001234ABCD",
        "platform_cert": payment_keys["cert_pem"],
        "wechat_pay_public_key": payment_keys["public_pem"],
        "notify_url": ORDER.notify_url or "",
    }


def stripe_credentials() -> dict[str, str]:
    return {
        "secret_key": "sk_test_KnowforgeFakeKey0123456789",
        "webhook_signing_secret": "whsec_KnowforgeFakeSecret0123456789",
    }


def order_request(**overrides: Any) -> OrderRequest:
    return OrderRequest(**{**ORDER.__dict__, **overrides})


def sign_with(private_pem: str, message: str) -> str:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return base64.b64encode(key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()


def test_cent_and_yuan_conversions_never_lose_a_fen():
    """金额换错一个分就是资损，边界值逐个钉死。"""
    assert alipay.cent_to_yuan(10000) == "100.00"
    assert alipay.cent_to_yuan(1) == "0.01"
    assert alipay.cent_to_yuan(999) == "9.99"
    assert alipay.cent_to_yuan(0) == "0.00"
    assert yuan_to_cent("100.00") == 10000
    assert yuan_to_cent("0.1") == 10
    assert yuan_to_cent("7") == 700
    assert yuan_to_cent("") is None
    assert yuan_to_cent(None) is None
    assert yuan_to_cent("1.2.3") is None
    assert yuan_to_cent("x") is None


def test_common_params_escapes_non_ascii_in_biz_content(payment_keys):
    """真网关实测：biz_content 带未转义的中文会吃 isv.invalid-signature。

    签名算在 UTF-8 字节上，网关按 charset 解表单时还原出的字节与我们签的那串不一致，
    所以发出去的参数串必须保持 ASCII，subject 用 \\uXXXX 表达（网关解出来仍是中文）。
    """
    params = alipay.common_params(
        app_id="2021000000000000",
        method="alipay.trade.precreate",
        biz_content={"subject": "额度充值 · 标准档", "out_trade_no": "KF0001"},
        timestamp="2026-09-27 10:00:00",
    )
    assert params["biz_content"].isascii(), params["biz_content"]
    assert json.loads(params["biz_content"])["subject"] == "额度充值 · 标准档"
    signed = alipay.sign_params(params, payment_keys["private_pem"])
    # 签名串本身也要能按 ASCII 送出去，否则 httpx 的百分号编码又会引入同一类分歧。
    assert alipay.request_sign_content(signed).isascii()


async def test_alipay_precreate_returns_a_qr_code_not_a_claim_of_payment(monkeypatch, payment_keys):
    """预下单只产出二维码串，收到钱与否留给回调或查单，所以不该有 provider 交易号。"""

    def handler(method: str, url: str, data: dict[str, str], headers: dict[str, str]) -> StubResponse:
        assert method == "POST" and url == "https://openapi.alipay.com/gateway.do"
        assert data["method"] == "alipay.trade.precreate"
        assert data["notify_url"] == ORDER.notify_url
        assert json.loads(data["biz_content"]) == {
            "out_trade_no": ORDER.out_trade_no,
            "total_amount": "100.00",
            "subject": ORDER.subject,
        }
        # 签名要能按"升序拼 k=v"的规则从发出去的参数里验回来，否则厂商第一步就会拒。
        assert alipay.verify(payment_keys["public_pem"], alipay.request_sign_content(data), data["sign"])
        return StubResponse(
            200,
            signed_node_response(
                "alipay_trade_precreate_response",
                {
                    "code": "10000",
                    "msg": "Success",
                    "out_trade_no": ORDER.out_trade_no,
                    "qr_code": "https://qr.alipay.com/kf001",
                },
                payment_keys["private_pem"],
            ),
        )

    stub = install(monkeypatch, alipay, handler)
    ticket = await alipay.create_order(alipay_credentials(payment_keys), ORDER)
    assert ticket.code_url == "https://qr.alipay.com/kf001"
    assert ticket.redirect_url is None and ticket.provider_trade_no is None
    assert stub.calls[0]["url"] == "https://openapi.alipay.com/gateway.do"


async def test_alipay_precreate_failure_carries_the_provider_reason(monkeypatch, payment_keys):
    """没签约当面付时的 sub_code 是唯一能让人知道该去开通什么的线索，不能糊成"下单失败"。"""
    install(
        monkeypatch,
        alipay,
        lambda *a: StubResponse(
            200,
            signed_node_response(
                "alipay_trade_precreate_response",
                {
                    "code": "40004",
                    "sub_code": "isv.insufficient-isv-permissions",
                    "sub_msg": "ISV权限不足",
                },
                payment_keys["private_pem"],
            ),
        ),
    )
    try:
        await alipay.create_order(alipay_credentials(payment_keys), ORDER)
    except ProviderError as reason:
        assert "isv.insufficient-isv-permissions" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("厂商拒单必须抛错")


async def test_alipay_query_order_refuses_to_report_payment_without_a_verified_response(
    monkeypatch, payment_keys
):
    def handler(method: str, url: str, data: dict[str, str], headers: dict[str, str]) -> StubResponse:
        return StubResponse(
            200,
            signed_node_response(
                "alipay_trade_query_response",
                {
                    "code": "10000",
                    "msg": "Success",
                    "out_trade_no": ORDER.out_trade_no,
                    "trade_no": "2026092722001000000001",
                    "trade_status": "TRADE_SUCCESS",
                    "buyer_pay_amount": "100.00",
                },
                payment_keys["private_pem"],
            ),
        )

    install(monkeypatch, alipay, handler)
    state = await alipay.query_order(alipay_credentials(payment_keys), ORDER.out_trade_no)
    assert state.paid and state.amount_cent == 10000
    assert state.provider_trade_no == "2026092722001000000001"
    assert "TRADE_SUCCESS" in state.detail

    # 换了把不相干的公钥，同一条响应就不能再算已付：未验签的报文拿来记账等于任人伪造。
    other = dict(alipay_credentials(payment_keys), alipay_public_key=payment_keys["cert_pem"])
    mismatched = await alipay.query_order(other, ORDER.out_trade_no)
    assert not mismatched.paid and "验签失败" in mismatched.detail

    install(monkeypatch, alipay, lambda *a: StubResponse(200, UNSIGNED))
    unverified = await alipay.query_order(alipay_credentials(payment_keys), ORDER.out_trade_no)
    assert not unverified.paid and "签名" in unverified.detail


async def test_alipay_notify_verifies_with_the_public_key_or_the_certificate(monkeypatch, payment_keys):
    form = {
        "app_id": "2021000000000000",
        "out_trade_no": ORDER.out_trade_no,
        "trade_no": "2026092722001000000002",
        "trade_status": "TRADE_FINISHED",
        "total_amount": "100.00",
        "sign_type": "RSA2",
        "charset": "utf-8",
    }
    form["sign"] = sign_with(payment_keys["private_pem"], alipay.notify_sign_content(form))
    raw_body = urllib.parse.urlencode(form).encode()

    result = alipay.parse_notify(
        alipay_credentials(payment_keys), NotifyRequest(headers={}, raw_body=raw_body, form=form)
    )
    assert result.out_trade_no == ORDER.out_trade_no and result.paid
    assert result.amount_cent == 10000 and result.provider_trade_no == "2026092722001000000002"
    assert result.digest == hashlib.sha256(raw_body).hexdigest()

    # 证书模式没有"支付宝公钥"这一项，验签公钥要从支付宝公钥证书里抽出来。
    cert_only = dict(alipay_credentials(payment_keys))
    cert_only.pop("alipay_public_key")
    cert_only["alipay_public_cert"] = payment_keys["cert_pem"]
    assert alipay.parse_notify(cert_only, NotifyRequest(headers={}, raw_body=raw_body, form=form)).paid

    try:
        alipay.parse_notify(
            cert_only,
            NotifyRequest(headers={}, raw_body=raw_body, form={**form, "total_amount": "9999.00"}),
        )
    except ProviderError as reason:
        assert "验签失败" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("改掉金额后签名不应该还成立")

    naked = dict(cert_only)
    naked.pop("alipay_public_cert")
    try:
        alipay.parse_notify(naked, NotifyRequest(headers={}, raw_body=raw_body, form=form))
    except ProviderError as reason:
        assert "无法校验回调签名" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("既没公钥也没证书时不许放行回调")


async def test_wechat_native_order_signs_the_body_and_returns_a_code_url(monkeypatch, payment_keys):
    def handler(method: str, url: str, data: dict[str, str], headers: dict[str, str]) -> StubResponse:
        return StubResponse(200, '{"code_url":"weixin://wxpay/bizpayurl?pr=KnowForge"}')

    stub = install(monkeypatch, wechat, handler)
    ticket = await wechat.create_order(wechat_credentials(payment_keys), ORDER)
    assert ticket.code_url == "weixin://wxpay/bizpayurl?pr=KnowForge"
    assert stub.calls[0]["method"] == "POST"
    assert stub.calls[0]["url"] == "https://api.mch.weixin.qq.com/v3/pay/transactions/native"
    assert stub.calls[0]["data"] == {
        "appid": "wx0123456789abcdef",
        "mchid": "1600000000",
        "description": ORDER.subject,
        "out_trade_no": ORDER.out_trade_no,
        "notify_url": ORDER.notify_url,
        "amount": {"total": 10000, "currency": "CNY"},
    }
    assert stub.calls[0]["headers"]["Content-Type"] == "application/json"
    assert stub.calls[0]["headers"]["Authorization"].startswith(
        'WECHATPAY2-SHA256-RSA2048 mchid="1600000000"'
    )

    try:
        await wechat.create_order(
            {k: v for k, v in wechat_credentials(payment_keys).items() if k != "app_id"}, ORDER
        )
    except ProviderError as reason:
        assert "缺少配置项" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("缺 app_id 时不应该发请求")

    try:
        await wechat.create_order(wechat_credentials(payment_keys), order_request(notify_url=None))
    except ProviderError as reason:
        assert "回调" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("没有回调地址的 Native 下单厂商会直接拒")


async def test_wechat_query_order_uses_the_merchant_order_number(monkeypatch, payment_keys):
    def handler(method: str, url: str, data: dict[str, str], headers: dict[str, str]) -> StubResponse:
        assert method == "GET"
        return StubResponse(
            200,
            json.dumps(
                {
                    "trade_state": "SUCCESS",
                    "trade_state_desc": "支付成功",
                    "transaction_id": "4200000000202609270000000001",
                    "out_trade_no": ORDER.out_trade_no,
                    "amount": {"total": 10000, "payer_total": 10000, "currency": "CNY"},
                }
            ),
        )

    stub = install(monkeypatch, wechat, handler)
    state = await wechat.query_order(wechat_credentials(payment_keys), ORDER.out_trade_no)
    assert state.paid and state.amount_cent == 10000
    assert state.provider_trade_no == "4200000000202609270000000001"
    assert "trade_state=SUCCESS" in state.detail
    assert stub.calls[0]["url"].endswith(
        f"/v3/pay/transactions/out-trade-no/{ORDER.out_trade_no}?mchid=1600000000"
    )

    install(monkeypatch, wechat, lambda *a: StubResponse(200, '{"trade_state":"NOTPAY"}'))
    pending = await wechat.query_order(wechat_credentials(payment_keys), ORDER.out_trade_no)
    assert not pending.paid and "NOTPAY" in pending.detail

    install(monkeypatch, wechat, lambda *a: StubResponse(404, '{"code":"ORDER_NOT_EXIST"}'))
    try:
        await wechat.query_order(wechat_credentials(payment_keys), ORDER.out_trade_no)
    except ProviderError as reason:
        assert "ORDER_NOT_EXIST" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("查单失败要抛错，不能当成未支付")


def wechat_notify_payload(
    payment_keys: dict[str, str], trade_state: str = "SUCCESS"
) -> tuple[bytes, dict[str, str]]:
    plain = json.dumps(
        {
            "out_trade_no": ORDER.out_trade_no,
            "transaction_id": "4200000000202609270000000002",
            "trade_state": trade_state,
            "amount": {"total": 10000, "payer_total": 10000, "currency": "CNY"},
        },
        separators=(",", ":"),
    )
    blob = encrypt_certificate(APIV3_KEY, plain)
    raw = json.dumps(
        {"id": "evt-knowforge", "event_type": "TRANSACTION.SUCCESS", "resource": blob},
        separators=(",", ":"),
    ).encode()
    timestamp, nonce = str(int(time.time())), "NONCEKNOWFORGE01"
    headers = {
        "wechatpay-timestamp": timestamp,
        "wechatpay-nonce": nonce,
        "wechatpay-signature": sign_with(
            payment_keys["private_pem"], wechat.notify_message(timestamp, nonce, raw)
        ),
        "wechatpay-serial": "000000001234ABCD",
    }
    return raw, headers


def test_wechat_notify_verifies_then_decrypts_and_fingerprints(payment_keys):
    raw, headers = wechat_notify_payload(payment_keys)
    credentials = wechat_credentials(payment_keys)
    result = wechat.parse_notify(credentials, NotifyRequest(headers=headers, raw_body=raw, form={}))
    assert result.out_trade_no == ORDER.out_trade_no and result.paid
    assert result.amount_cent == 10000 and result.provider_trade_no == "4200000000202609270000000002"
    assert result.digest == hashlib.sha256(raw).hexdigest()

    # 报文改一个字节、签名没重算 → 验签就该挡下来（金额在密文里，能改的是外层报文）。
    tampered = raw.replace(b"TRANSACTION.SUCCESS", b"TRANSACTION.CANCEL")
    try:
        wechat.parse_notify(credentials, NotifyRequest(headers=headers, raw_body=tampered, form={}))
    except ProviderError as reason:
        assert "验签失败" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("篡改金额后验签应该失败")

    missing = {key: value for key, value in headers.items() if not key.startswith("wechatpay-s")}
    try:
        wechat.parse_notify(credentials, NotifyRequest(headers=missing, raw_body=raw, form={}))
    except ProviderError as reason:
        assert "缺少验签所需的头" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("缺头不能当成验签通过")


def test_wechat_notify_picks_the_key_by_the_serial_prefix(payment_keys):
    """微信支付公钥模式的序列号带 PUB_KEY_ID_ 前缀，走平台证书那条路会永远对不上。"""
    raw, headers = wechat_notify_payload(payment_keys)
    public_key_mode = {k: v for k, v in wechat_credentials(payment_keys).items() if k != "platform_cert"}
    verified = wechat.parse_notify(
        public_key_mode,
        NotifyRequest(
            headers=dict(headers, **{"wechatpay-serial": "PUB_KEY_ID_0123"}), raw_body=raw, form={}
        ),
    )
    assert verified.paid and verified.out_trade_no == ORDER.out_trade_no
    # 同一个前缀下没有公钥就报缺头，而不是退回平台证书。
    no_material = {k: v for k, v in public_key_mode.items() if k != "wechat_pay_public_key"}
    try:
        wechat.parse_notify(
            no_material,
            NotifyRequest(
                headers=dict(headers, **{"wechatpay-serial": "PUB_KEY_ID_0123"}), raw_body=raw, form={}
            ),
        )
    except ProviderError as reason:
        assert "缺少验签所需的头" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("没有验签材料时必须拒掉")
    # 序列号不是本地那张证书的，说明平台证书该轮换了，不能拿旧公钥硬验。
    try:
        wechat.parse_notify(
            wechat_credentials(payment_keys),
            NotifyRequest(
                headers=dict(headers, **{"wechatpay-serial": "00000000DEADBEEF"}), raw_body=raw, form={}
            ),
        )
    except ProviderError as reason:
        assert "重新自检" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("证书序列号不符要提示轮换")


async def test_stripe_checkout_orders_send_form_fields_and_bill_in_fen(monkeypatch):
    def handler(method: str, url: str, data: dict[str, str], headers: dict[str, str]) -> StubResponse:
        return StubResponse(
            200, '{"id":"cs_test_knowforge","url":"https://checkout.stripe.com/c/cs_test_knowforge"}'
        )

    stub = install(monkeypatch, stripe, handler)
    ticket = await stripe.create_order(stripe_credentials(), ORDER)
    assert ticket.redirect_url == "https://checkout.stripe.com/c/cs_test_knowforge"
    assert ticket.provider_trade_no == "cs_test_knowforge"
    sent = stub.calls[0]["data"]
    assert stub.calls[0]["url"] == "https://api.stripe.com/v1/checkout/sessions"
    assert sent["mode"] == "payment" and sent["client_reference_id"] == ORDER.out_trade_no
    assert sent["line_items[0][price_data][unit_amount]"] == "10000"
    assert sent["line_items[0][price_data][currency]"] == "cny"
    assert sent["metadata[out_trade_no]"] == ORDER.out_trade_no

    try:
        await stripe.create_order(stripe_credentials(), order_request(return_url=None))
    except ProviderError as reason:
        assert "跳回地址" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("Stripe 没有 success_url 会被拒")

    install(monkeypatch, stripe, lambda *a: StubResponse(400, '{"error":{"message":"Invalid price"}}'))
    try:
        await stripe.create_order(stripe_credentials(), ORDER)
    except ProviderError as reason:
        assert "Invalid price" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("下单失败要带厂商原因抛出")


async def test_stripe_query_and_webhook_both_key_off_the_merchant_order_number(monkeypatch):
    session = {
        "id": "cs_test_knowforge",
        "client_reference_id": ORDER.out_trade_no,
        "payment_status": "paid",
        "amount_total": 10000,
        "status": "complete",
    }
    install(monkeypatch, stripe, lambda *a: StubResponse(200, json.dumps({"data": [session]})))
    state = await stripe.query_order(stripe_credentials(), ORDER.out_trade_no)
    assert state.paid and state.amount_cent == 10000 and state.provider_trade_no == "cs_test_knowforge"

    install(monkeypatch, stripe, lambda *a: StubResponse(200, '{"data":[]}'))
    assert not (await stripe.query_order(stripe_credentials(), ORDER.out_trade_no)).paid

    credentials = dict(stripe_credentials(), webhook_signing_secret="whsec_test_secret")
    raw = json.dumps({"type": "checkout.session.completed", "data": {"object": session}}).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(b"whsec_test_secret", f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
    notify = NotifyRequest(
        headers={"stripe-signature": f"t={timestamp},v1={signature}"}, raw_body=raw, form={}
    )
    result = stripe.parse_notify(credentials, notify)
    assert result.out_trade_no == ORDER.out_trade_no and result.paid and result.amount_cent == 10000
    try:
        stripe.parse_notify(
            credentials, NotifyRequest(headers={"stripe-signature": "t=1,v1=deadbeef"}, raw_body=raw, form={})
        )  # type: ignore[arg-type]
    except ProviderError as reason:
        assert "签名无效" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("坏 webhook 签名必须拒")


def test_registry_offers_the_same_channel_types_for_every_stage():
    """能自检却不能下单的渠道会让列表显示"已验证"却下出失败的单，四张表必须同一批键。"""
    keys = {
        frozenset(table)
        for table in (
            registry.SELF_CHECKS,
            registry.ORDER_CREATORS,
            registry.ORDER_QUERIES,
            registry.NOTIFY_PARSERS,
        )
    }
    assert len(keys) == 1
    assert next(iter(keys)) == {"alipay", "wechat", "stripe"}
    assert registry.supports_orders("custom") is False
