import asyncio
import base64
import json
import secrets
import time
from typing import Any

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services.payments.providers.base import (
    CHECK_TIMEOUT_SECONDS,
    Check,
    ProviderError,
    SelfCheck,
    require,
)

APIV3_HOST = "https://api.mch.weixin.qq.com"
AUTH_SCHEME = "WECHATPAY2-SHA256-RSA2048"
CERTIFICATES_PATH = "/v3/certificates"
NONCE_BYTES = 16


def load_private_key(pem: str) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ProviderError("商户API私钥必须是 RSA 私钥")
    return key


def nonce_str() -> str:
    return secrets.token_hex(NONCE_BYTES).upper()


def request_message(method: str, path: str, timestamp: str, nonce: str, body: str) -> str:
    """请求签名串是五段换行分隔结构，末尾那个空行也是内容的一部分，少一个字节就验不过。"""
    return f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n"


def notify_message(timestamp: str, nonce: str, raw_body: bytes) -> str:
    """回调验签串只有三段：时间戳、随机串、原始报文。请求签名那套五段结构不适用于这里。"""
    return f"{timestamp}\n{nonce}\n{raw_body.decode()}\n"


def sign(private_key_pem: str, message: str) -> str:
    signature = load_private_key(private_key_pem).sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


def authorization(
    *,
    method: str,
    path: str,
    body: str,
    mch_id: str,
    serial_no: str,
    private_key_pem: str,
    timestamp: str,
    nonce: str,
) -> str:
    value = sign(private_key_pem, request_message(method, path, timestamp, nonce, body))
    return (
        f'{AUTH_SCHEME} mchid="{mch_id}",nonce_str="{nonce}",timestamp="{timestamp}",'
        f'serial_no="{serial_no}",signature="{value}"'
    )


def _public_key(material: str) -> Any:
    stripped = material.strip()
    if stripped.startswith("-----BEGIN CERTIFICATE"):
        return x509.load_pem_x509_certificate(stripped.encode()).public_key()
    return serialization.load_pem_public_key(stripped.encode())


def verify(public_key_material: str, message: str, signature: str) -> bool:
    try:
        _public_key(public_key_material).verify(
            base64.b64decode(signature), message.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
    except Exception:  # noqa: BLE001 - 坏签名、坏公钥、坏 base64 都算不通过，不区分原因
        return False
    return True


def certificate_serial(pem: str) -> str:
    return f"{x509.load_pem_x509_certificate(pem.encode()).serial_number:016X}"


def decrypt_resource(apiv3_key: str, associated_data: str, nonce: str, ciphertext: str) -> str:
    """平台证书与回调 resource 都用 APIv3 密钥做 AES-256-GCM 解密，AAD 是 associated_data。"""
    key = apiv3_key.encode()
    if len(key) != 32:
        raise ProviderError("APIv3 密钥必须恰好 32 字节")
    return (
        AESGCM(key).decrypt(nonce.encode(), base64.b64decode(ciphertext), associated_data.encode()).decode()
    )


async def request_json(path: str, credentials: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
    """带商户签名的 GET，返回状态码、原始响应体与响应头（响应验签要用头里的签名与序列号）。"""
    require(credentials, "wechat", "mch_id", "cert_serial_no", "merchant_private_key")
    timestamp, nonce = str(int(time.time())), nonce_str()
    header = await asyncio.to_thread(
        authorization,
        method="GET",
        path=path,
        body="",
        mch_id=credentials["mch_id"],
        serial_no=credentials["cert_serial_no"],
        private_key_pem=credentials["merchant_private_key"],
        timestamp=timestamp,
        nonce=nonce,
    )
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        response = await client.get(
            APIV3_HOST + path, headers={"Authorization": header, "Accept": "application/json"}
        )
    return (
        response.status_code,
        response.content,
        {key.lower(): value for key, value in response.headers.items()},
    )


async def fetch_platform_certificates(credentials: dict[str, str]) -> tuple[list[dict[str, str]], bool]:
    """拉取并解密平台证书。

    这个接口只需要商户侧签名，所以"能拿到并解密出证书 + 响应能被证书验过"就是商户号、
    证书序列号、私钥、APIv3 密钥四样东西同时对的最便宜证明。
    """
    require(credentials, "wechat", "apiv3_key")
    status, raw, headers = await request_json(CERTIFICATES_PATH, credentials)
    if status != 200:
        raise ProviderError(f"获取平台证书失败 HTTP {status}：{raw.decode()[:200]}")
    payload: dict[str, Any] = json.loads(raw)
    items = payload.get("data") or []
    certificates: list[dict[str, str]] = []
    for item in items:
        resource = item.get("encrypt_certificate") or {}
        pem = await asyncio.to_thread(
            decrypt_resource,
            credentials["apiv3_key"],
            str(resource.get("associated_data") or ""),
            str(resource.get("nonce") or ""),
            str(resource.get("ciphertext") or ""),
        )
        certificates.append({"serial_no": str(item.get("serial_no") or ""), "pem": pem})
    if not certificates:
        return [], False
    # 引导信任：用刚取回的证书验这次响应的签名，公钥和报文都来自同一条信道，验过才敢用。
    signature = headers.get("wechatpay-signature", "")
    message = notify_message(headers.get("wechatpay-timestamp", ""), headers.get("wechatpay-nonce", ""), raw)
    verified = bool(signature) and await asyncio.to_thread(verify, certificates[0]["pem"], message, signature)
    return certificates, verified


async def self_check(credentials: dict[str, str]) -> SelfCheck:
    require(credentials, "wechat", "mch_id", "app_id", "apiv3_key", "merchant_private_key", "cert_serial_no")
    checks: list[Check] = []
    try:
        key = await asyncio.to_thread(load_private_key, credentials["merchant_private_key"])
    except (ProviderError, ValueError) as reason:
        return SelfCheck("local_only", (Check("商户API私钥可解析", False, str(reason)[:200]),))
    checks.append(Check("商户API私钥可解析", True, f"RSA-{key.key_size} 位"))
    notify_url = credentials.get("notify_url") or ""
    if not notify_url:
        checks.append(Check("支付回调地址", True, "未配置回调，入账依赖主动查单"))
    elif not notify_url.startswith("https://"):
        checks.append(Check("支付回调地址", False, "微信只接受 https 公网回调地址，http 隧道会被直接拒"))
    else:
        checks.append(Check("支付回调地址", True, notify_url))
    try:
        certificates, verified = await fetch_platform_certificates(credentials)
    except (ProviderError, httpx.HTTPError, json.JSONDecodeError) as reason:
        checks.append(Check("商户签名被 APIv3 接受", False, str(reason)[:300]))
        return SelfCheck("live", tuple(checks))
    if not certificates:
        # 空列表说明这个商户走"微信支付公钥"模式，平台证书这条路本来就不存在。
        if credentials.get("wechat_pay_public_key") and credentials.get("wechat_pay_public_key_id"):
            checks.append(Check("商户签名被 APIv3 接受", True, "无平台证书，回调按已配置的微信支付公钥验签"))
            return SelfCheck("live", tuple(checks))
        checks.append(Check("商户签名被 APIv3 接受", True, "签名有效"))
        checks.append(Check("微信侧验签公钥", False, "该商户无平台证书，需填微信支付公钥与公钥ID"))
        return SelfCheck("live", tuple(checks))
    checks.append(Check("商户签名被 APIv3 接受", True, f"取回 {len(certificates)} 张平台证书"))
    chosen = certificates[0]
    serial = chosen["serial_no"] or certificate_serial(chosen["pem"])
    checks.append(Check("响应验签与证书解密", verified, f"证书序列号 {serial}"))
    stores = {"platform_cert": chosen["pem"].strip()} if verified else None
    return SelfCheck("live", tuple(checks), stores)
