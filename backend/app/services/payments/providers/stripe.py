import hashlib
import hmac
import json
import time

import httpx

from app.services.payments.providers.base import (
    CHECK_TIMEOUT_SECONDS,
    Check,
    SelfCheck,
    require,
)

API_HOST = "https://api.stripe.com"
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


async def self_check(credentials: dict[str, str]) -> SelfCheck:
    require(credentials, "stripe", "secret_key")
    secret = credentials["secret_key"]
    checks = [Check("密钥环境", True, f"{environment(secret)} 模式")]
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        response = await client.get(f"{API_HOST}/v1/balance", headers={"Authorization": f"Bearer {secret}"})
    if response.status_code == 200:
        payload = json.loads(response.text)
        available = (payload.get("available") or [{}])[0]
        checks.append(
            Check("Secret Key 被接受", True, f"账户余额币种 {available.get('currency', '—')}".upper())
        )
    else:
        message = (response.json().get("error") or {}).get("message") if response.content else ""
        checks.append(Check("Secret Key 被接受", False, f"HTTP {response.status_code} {message}"[:300]))
    if not credentials.get("webhook_signing_secret", "").startswith("whsec_"):
        checks.append(Check("Webhook 签名密钥", False, "缺少 whsec_ 密钥，回调无法验签"))
    else:
        checks.append(Check("Webhook 签名密钥", True, "已配置（格式校验，实际生效以首次回调为准）"))
    return SelfCheck("live", tuple(checks))
