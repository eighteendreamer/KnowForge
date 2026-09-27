import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.services.payments.providers.base import (
    CHECK_TIMEOUT_SECONDS,
    Check,
    NotifyRequest,
    NotifyResult,
    OrderRequest,
    OrderTicket,
    PaidState,
    ProviderError,
    SelfCheck,
    require,
)

API_HOST = "https://api.stripe.com"
CURRENCY = "cny"
# 与官方 SDK 默认一致：时间戳偏差超过 5 分钟就拒绝，挡住重放。
WEBHOOK_TOLERANCE_SECONDS = 300


def environment(secret_key: str) -> str:
    return "live" if secret_key.startswith("sk_live_") else "test"


def _parse_header(header: str) -> tuple[str, list[str]]:
    timestamp = ""
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.partition("=")
        if key.strip() == "t":
            timestamp = value.strip()
        elif key.strip() == "v1":
            signatures.append(value.strip())
    return timestamp, signatures


def verify_webhook(raw_body: bytes, header: str, secret: str, now: float | None = None) -> bool:
    """Stripe 用 HMAC-SHA256("{t}.{原始报文}") 签名，所以必须拿原始字节来验，不能先解析再序列化。"""
    if not header or not secret:
        return False
    timestamp, signatures = _parse_header(header)
    if not timestamp or not signatures:
        return False
    try:
        skew = abs((now if now is not None else time.time()) - float(timestamp))
    except ValueError:
        return False
    if skew > WEBHOOK_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, value) for value in signatures)


async def call(
    method: str, credentials: dict[str, str], path: str, form: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    """Stripe 的写接口收 x-www-form-urlencoded、键用 a[b][c] 形式，不能发 JSON。"""
    require(credentials, "stripe", "secret_key")
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        response = await client.request(
            method,
            API_HOST + path,
            headers={"Authorization": f"Bearer {credentials['secret_key']}"},
            data=form,
        )
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        payload = {}
    return response.status_code, payload if isinstance(payload, dict) else {}


def _error_message(payload: dict[str, Any]) -> str:
    return str((payload.get("error") or {}).get("message") or "")[:200]


async def create_order(credentials: dict[str, str], order: OrderRequest) -> OrderTicket:
    """Checkout Session：托管收银台，我们只拿到一个跳转地址，卡号不过我们的服务器。"""
    if not order.return_url:
        raise ProviderError("Stripe 下单必须配置支付完成后的跳回地址")
    status, payload = await call(
        "POST",
        credentials,
        "/v1/checkout/sessions",
        {
            "mode": "payment",
            "client_reference_id": order.out_trade_no,
            "success_url": order.return_url,
            "cancel_url": order.return_url,
            "metadata[out_trade_no]": order.out_trade_no,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": CURRENCY,
            "line_items[0][price_data][unit_amount]": str(order.amount_cent),
            "line_items[0][price_data][product_data][name]": order.subject[:100],
        },
    )
    if status != 200:
        raise ProviderError(f"Stripe 下单失败 HTTP {status}：{_error_message(payload)}")
    session_url = payload.get("url")
    if not session_url:
        raise ProviderError("Stripe 下单响应里没有收银台地址")
    return OrderTicket(redirect_url=str(session_url), provider_trade_no=payload.get("id"))


async def query_order(credentials: dict[str, str], out_trade_no: str) -> PaidState:
    """按 client_reference_id 反查会话。下单时没存 session id 或存的值丢了也能对得上账。"""
    status, payload = await call(
        "GET",
        credentials,
        "/v1/checkout/sessions",
        {"client_reference_id": out_trade_no, "limit": "1"},
    )
    if status != 200:
        raise ProviderError(f"Stripe 查单失败 HTTP {status}：{_error_message(payload)}")
    sessions = payload.get("data") or []
    if not sessions:
        return PaidState(False, detail="Stripe 侧查不到该商户单号")
    session = sessions[0]
    payment_status = str(session.get("payment_status") or "")
    return PaidState(
        paid=payment_status == "paid",
        amount_cent=int(session["amount_total"]) if session.get("amount_total") is not None else None,
        provider_trade_no=session.get("id"),
        detail=f"payment_status={payment_status or '—'} status={session.get('status') or '—'}",
    )


def parse_notify(credentials: dict[str, str], notify: NotifyRequest) -> NotifyResult:
    """Webhook 用 HMAC 签整段原始报文，所以验签必须在解析之前、用原始字节做。"""
    secret = credentials.get("webhook_signing_secret", "")
    if not verify_webhook(notify.raw_body, notify.headers.get("stripe-signature", ""), secret):
        raise ProviderError("回调签名无效或时间戳超出容差")
    event = json.loads(notify.raw_body)
    session: dict[str, Any] = (event.get("data") or {}).get("object") or {}
    out_trade_no = str(
        session.get("client_reference_id") or (session.get("metadata") or {}).get("out_trade_no") or ""
    )
    payment_status = str(session.get("payment_status") or "")
    return NotifyResult(
        out_trade_no=out_trade_no,
        paid=payment_status == "paid" or str(event.get("type") or "") == "checkout.session.completed",
        amount_cent=int(session["amount_total"]) if session.get("amount_total") is not None else None,
        provider_trade_no=session.get("id"),
        digest=hashlib.sha256(notify.raw_body).hexdigest(),
        detail=f"type={event.get('type') or '—'} payment_status={payment_status or '—'}",
    )


async def self_check(credentials: dict[str, str]) -> SelfCheck:
    require(credentials, "stripe", "secret_key")
    secret = credentials["secret_key"]
    checks = [Check("密钥环境", True, f"{environment(secret)} 模式")]
    status, payload = await call("GET", credentials, "/v1/balance")
    if status == 200:
        available = (payload.get("available") or [{}])[0]
        checks.append(
            Check("Secret Key 被接受", True, f"账户余额币种 {available.get('currency', '—')}".upper())
        )
    else:
        checks.append(Check("Secret Key 被接受", False, f"HTTP {status} {_error_message(payload)}"[:300]))
    if not credentials.get("webhook_signing_secret", "").startswith("whsec_"):
        checks.append(Check("Webhook 签名密钥", False, "缺少 whsec_ 密钥，回调无法验签"))
    else:
        checks.append(Check("Webhook 签名密钥", True, "已配置（格式校验，实际生效以首次回调为准）"))
    return SelfCheck("live", tuple(checks))
