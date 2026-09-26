import asyncio
import base64
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from test_recharge_config import alipay_credentials, create_channel

from app.services.payments.providers import alipay, registry, stripe, wechat
from app.services.payments.providers.base import ProviderError

APIV3_KEY = "KnowforgeTestApiV3Key32Chars!!!!"


class StubHttp:
    """替掉厂商侧出站调用：仓库里没有 respx，桩风格沿用 test_pipeline_failures。"""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []
        self.HTTPError = httpx.HTTPError

    def AsyncClient(self, **_kwargs: Any) -> "StubHttp":
        return self

    async def __aenter__(self) -> "StubHttp":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def post(self, url: str, data: dict[str, str] | None = None, **kwargs: Any) -> Any:
        self.calls.append(
            {"method": "POST", "url": url, "data": data or {}, "headers": kwargs.get("headers", {})}
        )
        return self.handler("POST", url, data or {}, kwargs.get("headers", {}))

    async def get(self, url: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        self.calls.append({"method": "GET", "url": url, "data": {}, "headers": headers or {}})
        return self.handler("GET", url, {}, headers or {})


class StubResponse:
    def __init__(self, status_code: int = 200, text: str = "", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.content = text.encode()
        self.headers = headers or {}

    def json(self) -> Any:
        return json.loads(self.text)


def install(monkeypatch, module: Any, handler: Any) -> StubHttp:
    stub = StubHttp(handler)
    monkeypatch.setattr(module, "httpx", stub)
    return stub


def signed_node_response(node: str, payload: dict[str, Any], private_pem: str) -> str:
    """真网关的业务节点一定带 sign，桩不签就等于在测一条不存在的分支。

    待签名内容是节点自身的 JSON 原文（不含 "节点名": 前缀），与官方 SDK 的截串规则一致。
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    signature = key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA256())
    return json.dumps(
        {node: json.loads(body), "sign": base64.b64encode(signature).decode()},
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def test_alipay_self_check_treats_trade_not_exist_as_proof_of_working_keys(monkeypatch, payment_keys):
    def handler(method: str, url: str, data: dict[str, str], headers: dict[str, str]) -> StubResponse:
        assert method == "POST" and url == "https://openapi.alipay.com/gateway.do"
        assert data["method"] == "alipay.trade.query" and data["sign"]
        return StubResponse(
            200,
            signed_node_response(
                "alipay_trade_query_response",
                {"code": "40004", "sub_code": "ACQ.TRADE_NOT_EXIST", "msg": "Business Failed"},
                payment_keys["private_pem"],
            ),
        )

    stub = install(monkeypatch, alipay, handler)
    result = await alipay.self_check(alipay_credentials(payment_keys))
    assert result.mode == "live" and result.passed, [(item.name, item.detail) for item in result.checks]
    assert stub.calls[0]["data"]["sign_type"] == "RSA2" and stub.calls[0]["data"]["charset"] == "utf-8"
    assert stub.calls[0]["data"]["app_id"] == "2021000000000000"


async def test_alipay_self_check_separates_unsigned_permission_rejection(monkeypatch, payment_keys):
    install(
        monkeypatch,
        alipay,
        lambda *_: StubResponse(
            200,
            json.dumps(
                {
                    "error_response": {
                        "code": "40002",
                        "sub_code": "isv.insufficient-isv-permissions",
                        "sub_msg": "未签约",
                    }
                }
            ),
        ),
    )
    result = await alipay.self_check(alipay_credentials(payment_keys))
    assert not result.passed
    assert any("未获得该接口权限" in item.name and "签约" in item.detail for item in result.checks)


def test_alipay_signature_follows_the_documented_construction(payment_keys):
    params = alipay.common_params(
        app_id="2021000000000000",
        method="alipay.trade.query",
        biz_content={"out_trade_no": "KF0001"},
        timestamp="2026-09-26 10:00:00",
    )
    signed = alipay.sign_params(params, payment_keys["private_pem"])
    expected = "&".join(f"{key}={params[key]}" for key in sorted(params))
    assert alipay.request_sign_content(params) == expected
    key = serialization.load_pem_private_key(payment_keys["private_pem"].encode(), password=None)
    assert key.sign(expected.encode(), padding.PKCS1v15(), hashes.SHA256()) == base64.b64decode(
        signed["sign"]
    )
    # 公钥能验过，才说明厂商侧拿同一套规则也能验过。
    assert alipay.verify(payment_keys["public_pem"], expected, signed["sign"])
    assert not alipay.verify(payment_keys["public_pem"], expected + "x", signed["sign"])


def test_alipay_notify_verification_rejects_a_tampered_amount(payment_keys):
    form = {
        "app_id": "2021000000000000",
        "out_trade_no": "KF0001",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "100.00",
        "sign_type": "RSA2",
        "charset": "utf-8",
    }
    message = alipay.notify_sign_content(form)
    key = serialization.load_pem_private_key(payment_keys["private_pem"].encode(), password=None)
    form["sign"] = base64.b64encode(key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
    assert alipay.verify_notify(form, payment_keys["public_pem"])
    tampered = {**form, "total_amount": "9999.00"}
    assert not alipay.verify_notify(tampered, payment_keys["public_pem"])
    assert not alipay.verify_notify({**form, "sign": ""}, payment_keys["public_pem"])


def encrypt_certificate(apiv3_key: str, pem: str) -> dict[str, str]:
    nonce = base64.b64encode(b"0123456789ab").decode()
    ciphertext = AESGCM(apiv3_key.encode()).encrypt(nonce.encode(), pem.encode(), b"certificate")
    return {
        "associated_data": "certificate",
        "nonce": nonce,
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }


def wechat_certificates_payload(payment_keys: dict[str, str]) -> tuple[str, dict[str, str]]:
    raw = json.dumps(
        {
            "data": [
                {
                    "serial_no": "000000001234ABCD",
                    "encrypt_certificate": encrypt_certificate(APIV3_KEY, payment_keys["cert_pem"]),
                }
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    timestamp, nonce = str(int(time.time())), "NONCE1234567890"
    key = serialization.load_pem_private_key(payment_keys["private_pem"].encode(), password=None)
    signature = base64.b64encode(
        key.sign(
            wechat.notify_message(timestamp, nonce, raw.encode()).encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    ).decode()
    headers = {
        "wechatpay-timestamp": timestamp,
        "wechatpay-nonce": nonce,
        "wechatpay-signature": signature,
        "wechatpay-serial": "000000001234ABCD",
    }
    return raw, headers


async def test_wechat_self_check_bootstraps_trust_from_the_certificates_endpoint(monkeypatch, payment_keys):
    raw, headers = wechat_certificates_payload(payment_keys)
    stub = install(monkeypatch, wechat, lambda *_: StubResponse(200, raw, headers))
    credentials = {
        "mch_id": "1600000000",
        "app_id": "wx0123456789abcdef",
        "apiv3_key": APIV3_KEY,
        "merchant_private_key": payment_keys["private_pem"],
        "cert_serial_no": "0123456789ABCDEF0123456789ABCDEF01234567",
        "notify_url": "https://pay.example.test/v1/payments/notify/wechat",
    }
    result = await wechat.self_check(credentials)
    assert result.mode == "live" and result.passed, [(item.name, item.detail) for item in result.checks]
    assert stub.calls[0]["url"] == "https://api.mch.weixin.qq.com/v3/certificates"
    authorization = stub.calls[0]["headers"]["Authorization"]
    assert authorization.startswith('WECHATPAY2-SHA256-RSA2048 mchid="1600000000"')
    assert 'serial_no="0123456789ABCDEF0123456789ABCDEF01234567"' in authorization
    assert result.stores and result.stores["platform_cert"] == payment_keys["cert_pem"].strip()


async def test_wechat_self_check_refuses_a_plain_http_tunnel(monkeypatch, payment_keys):
    stub = install(monkeypatch, wechat, lambda *_: StubResponse(200, '{"data":[]}'))
    credentials = {
        "mch_id": "1600000000",
        "app_id": "wx0123456789abcdef",
        "apiv3_key": APIV3_KEY,
        "merchant_private_key": payment_keys["private_pem"],
        "cert_serial_no": "0123456789ABCDEF01234567",
        "notify_url": "http://natapp.example.invalid/notify",
    }
    result = await wechat.self_check(credentials)
    assert not result.passed
    assert "https" in item_ok(result, "支付回调地址").detail
    # 回调地址不合格不代表密钥不合格，证书那一路照样要验，否则一次自检看不出两个问题。
    assert stub.calls[0]["url"].endswith("/v3/certificates")


def item_ok(result: Any, name: str) -> Any:
    return next(item for item in result.checks if item.name == name)


def test_wechat_request_and_notify_messages_are_different_structures(payment_keys):
    request = wechat.request_message("GET", "/v3/certificates", "1700000000", "NONCE", "")
    assert request == "GET\n/v3/certificates\n1700000000\nNONCE\n\n"
    notify = wechat.notify_message("1700000000", "NONCE", b'{"id":"1"}')
    assert notify == '1700000000\nNONCE\n{"id":"1"}\n'
    signature = wechat.sign(payment_keys["private_pem"], notify)
    assert wechat.verify(payment_keys["public_pem"], notify, signature)
    assert not wechat.verify(payment_keys["public_pem"], request, signature)


def test_wechat_decrypt_resource_uses_the_apiv3_key_and_associated_data(payment_keys):
    blob = encrypt_certificate(APIV3_KEY, payment_keys["cert_pem"])
    decrypted = wechat.decrypt_resource(APIV3_KEY, blob["associated_data"], blob["nonce"], blob["ciphertext"])
    assert decrypted == payment_keys["cert_pem"]
    assert wechat.certificate_serial(decrypted) == "000000001234ABCD"
    wrong = encrypt_certificate("WrongApiV3KeyForThisTest32Chars!", payment_keys["cert_pem"])
    try:
        wechat.decrypt_resource(APIV3_KEY, wrong["associated_data"], wrong["nonce"], wrong["ciphertext"])
    except Exception:
        pass
    else:  # pragma: no cover - 只有解密真的失败才算通过
        raise AssertionError("用错密钥的密文不应该解得开")


async def test_alipay_self_check_tells_the_operator_the_public_key_is_the_wrong_one(
    monkeypatch, payment_keys
):
    """网关受理了请求但响应验不过，几乎只会是"支付宝公钥"拷错了（拷成应用公钥或该用证书）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    install(
        monkeypatch,
        alipay,
        lambda *_: StubResponse(
            200,
            signed_node_response(
                "alipay_trade_query_response",
                {"code": "40004", "sub_code": "ACQ.TRADE_NOT_EXIST"},
                payment_keys["private_pem"],
            ),
        ),
    )
    result = await alipay.self_check({**alipay_credentials(payment_keys), "alipay_public_key": other})
    assert not result.passed
    mismatch = item_ok(result, "响应验签")
    assert not mismatch.ok and "支付宝公钥" in mismatch.detail and "证书" in mismatch.detail
    # 请求侧成功不能被响应侧失败抹掉：两条结论各自独立，运营才知道该动哪一项。
    assert item_ok(result, "网关受理签名且应用有效").ok


async def test_stripe_self_check_and_webhook_verification(monkeypatch, payment_keys):
    install(
        monkeypatch, stripe, lambda *_: StubResponse(200, '{"available":[{"currency":"cny","amount":0}]}')
    )
    result = await stripe.self_check(
        {
            "secret_key": "sk_test_KnowforgeFakeKey0123456789",
            "webhook_signing_secret": "whsec_KnowforgeFakeSecret0123456789",
        }
    )
    assert result.mode == "live" and result.passed
    assert any("test" in item.detail for item in result.checks)
    body = b'{"type":"checkout.session.completed"}'
    secret = "whsec_test_secret"
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    header = f"t={timestamp},v1={signature}"
    assert stripe.verify_webhook(body, header, secret)
    assert not stripe.verify_webhook(b'{"type":"other"}', header, secret)
    assert not stripe.verify_webhook(body, header, "whsec_wrong_wrong_wrong")
    stale = str(int(time.time()) - 305)
    old_signature = hmac.new(secret.encode(), f"{stale}.".encode() + body, hashlib.sha256).hexdigest()
    assert not stripe.verify_webhook(body, f"t={stale},v1={old_signature}", secret)


async def test_verify_endpoint_reports_provider_failure_as_a_result(context, payment_keys, monkeypatch):
    created = await create_channel(context, payment_keys)
    channel_id = created.json()["data"]["id"]
    install(
        monkeypatch,
        alipay,
        lambda *_: StubResponse(
            200, '{"error_response":{"code":"40002","sub_code":"isv.invalid-signature"}}'
        ),
    )
    response = await context["client"].post(
        f"/v1/admin/recharge/channels/{channel_id}/verify", headers=context["admin_headers"]
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["passed"] is False and payload["channel_type"] == "alipay"
    assert any(item["ok"] is False for item in payload["checks"])


async def test_self_check_fills_in_the_credential_the_operator_could_not_provide(
    context, payment_keys, monkeypatch
):
    """平台证书本来就该由系统去取，让人手填等于没接入。"""
    created = await create_channel(
        context,
        payment_keys,
        code="wechat-selfcheck",
        channel_type="wechat",
        credentials={
            "mch_id": "1600000000",
            "app_id": "wx0123456789abcdef",
            "apiv3_key": APIV3_KEY,
            "merchant_private_key": payment_keys["private_pem"],
            "cert_serial_no": "0123456789ABCDEF0123456789ABCDEF01234567",
            "notify_url": "https://pay.example.test/v1/payments/notify/wechat",
        },
        enabled=False,
    )
    assert created.status_code == 200, created.text
    channel_id = created.json()["data"]["id"]
    assert all(item["key"] != "platform_cert" for item in created.json()["data"]["credentials"])
    assert created.json()["data"]["configuration"]["problem"]

    raw, headers = wechat_certificates_payload(payment_keys)
    install(monkeypatch, wechat, lambda *_: StubResponse(200, raw, headers))
    response = await context["client"].post(
        f"/v1/admin/recharge/channels/{channel_id}/verify", headers=context["admin_headers"]
    )
    payload = response.json()["data"]
    assert payload["passed"] is True, payload["checks"]
    listed = await context["client"].get("/v1/admin/recharge/channels", headers=context["admin_headers"])
    channel = next(item for item in listed.json()["data"]["items"] if item["code"] == "wechat-selfcheck")
    cert = next(item for item in channel["credentials"] if item["key"] == "platform_cert")
    assert cert["secret"] is False and cert["value"].startswith("-----BEGIN CERTIFICATE-----")
    assert cert["key_version"] == 1 and channel["configuration"]["problem"] is None

    # 再自检一次不该把版本号推高：没变的东西被记成"刚换过"会让审计结论失真。
    await context["client"].post(
        f"/v1/admin/recharge/channels/{channel_id}/verify", headers=context["admin_headers"]
    )
    again = await context["client"].get("/v1/admin/recharge/channels", headers=context["admin_headers"])
    second = next(item for item in again.json()["data"]["items"] if item["code"] == "wechat-selfcheck")
    assert next(item for item in second["credentials"] if item["key"] == "platform_cert")["key_version"] == 1

    enabled = await context["client"].patch(
        f"/v1/admin/recharge/channels/{channel_id}", headers=context["admin_headers"], json={"enabled": True}
    )
    assert enabled.status_code == 200, enabled.text


async def test_self_check_keeps_the_event_loop_free(context, payment_keys, monkeypatch):
    """RSA 签名压在事件循环上会拖死整个后端，AGENTS 明令走 to_thread。"""
    created = await create_channel(context, payment_keys)
    channel_id = created.json()["data"]["id"]
    install(
        monkeypatch,
        alipay,
        lambda *_: StubResponse(
            200, '{"alipay_trade_query_response":{"code":"40004","sub_code":"ACQ.TRADE_NOT_EXIST"}}'
        ),
    )
    real = alipay.sign_params

    def slow_sign(params: dict[str, str], private_key: str) -> dict[str, str]:
        time.sleep(0.5)  # 代替真实 RSA 运算的 CPU 开销
        return real(params, private_key)

    monkeypatch.setattr(alipay, "sign_params", slow_sign)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        response = await context["client"].post(
            f"/v1/admin/recharge/channels/{channel_id}/verify", headers=context["admin_headers"]
        )
    finally:
        task.cancel()
    assert response.status_code == 200
    assert ticks >= 5, f"自检期间事件循环停摆了，只走了 {ticks} 拍"


async def test_registry_refuses_a_channel_type_that_cannot_be_verified(context, payment_keys):
    offline = await create_channel(
        context, payment_keys, code="bank-transfer", channel_type="custom", credentials={}, enabled=True
    )
    assert offline.status_code == 200, offline.text
    try:
        await registry.run_self_check("custom", {})
    except ProviderError as reason:
        assert "无需自检" in str(reason)
    else:  # pragma: no cover
        raise AssertionError("custom 类型不应该有自检实现")
