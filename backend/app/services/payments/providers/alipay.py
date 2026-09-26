import asyncio
import base64
import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.services.payments.providers.base import (
    CHECK_TIMEOUT_SECONDS,
    Check,
    SelfCheck,
    require,
)

# 支付宝按 "yyyy-MM-dd HH:mm:ss" 校验 timestamp 与服务器时差，签名串里必须用东八区时间；
# 直接取本机时间会在部署到非 CST 机器时报 Invalid signature，所以显式钉住时区。
CHINA_TZ = ZoneInfo("Asia/Shanghai")
SELF_CHECK_OUT_TRADE_NO = "KFSELFCHECK00000000000000000"
# 通知验签要剔除的两个键。请求签名只剔 sign（sign_type 参与签名），两者规则不同，别混用。
NOTIFY_EXCLUDED_KEYS = frozenset({"sign", "sign_type"})
REQUEST_EXCLUDED_KEYS = frozenset({"sign"})
PERMISSION_SUB_CODES = (
    "isv.insufficient-isv-permissions",
    "isv.app-id-not-matched",
    "isv.access-control-limit",
)


def load_private_key(pem: str) -> Any:
    return serialization.load_pem_private_key(pem.encode(), password=None)


def load_public_key(material: str) -> Any:
    """控制台给的是裸 base64，规范化入库后是 PEM，两种都要能吃。"""
    if material.startswith("-----BEGIN"):
        return serialization.load_pem_public_key(material.encode())
    return serialization.load_der_public_key(base64.b64decode(material))


def _joined(params: dict[str, str], excluded: frozenset[str]) -> str:
    included = {key: value for key, value in params.items() if value and key not in excluded}
    return "&".join(f"{key}={included[key]}" for key in sorted(included))


def request_sign_content(params: dict[str, str]) -> str:
    """待签名串：按 key 升序拼 k=v、值不做 URL 编码、剔除空值与 sign。

    与官方 Python SDK 的 get_sign_content 一致：公共参数（含 sign_type、format）一起参与签名。
    """
    return _joined(params, REQUEST_EXCLUDED_KEYS)


def notify_sign_content(form: dict[str, str]) -> str:
    return _joined(form, NOTIFY_EXCLUDED_KEYS)


def sign_params(params: dict[str, str], private_key_pem: str) -> dict[str, str]:
    signature = load_private_key(private_key_pem).sign(
        request_sign_content(params).encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    return {**params, "sign": base64.b64encode(signature).decode()}


def verify(public_key_material: str, message: str, signature: str) -> bool:
    try:
        load_public_key(public_key_material).verify(
            base64.b64decode(signature), message.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
    except Exception:  # noqa: BLE001 - 验签失败包括坏签名、坏公钥、坏 base64，一律算不通过
        return False
    return True


def verify_notify(form: dict[str, str], public_key_material: str) -> bool:
    signature = form.get("sign")
    if not signature or not public_key_material:
        return False
    return verify(public_key_material, notify_sign_content(form), signature)


def common_params(
    *,
    app_id: str,
    method: str,
    biz_content: dict[str, Any],
    timestamp: str,
    notify_url: str | None = None,
    return_url: str | None = None,
) -> dict[str, str]:
    params = {
        "app_id": app_id,
        "method": method,
        "format": "json",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": timestamp,
        "version": "1.0",
        "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
    }
    if notify_url:
        params["notify_url"] = notify_url
    if return_url:
        params["return_url"] = return_url
    return params


def page_pay_url(gateway: str, params: dict[str, str]) -> str:
    return f"{gateway}?{urllib.parse.urlencode(params)}"


def now_stamp() -> str:
    return datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")


def extract_node(raw: str, node: str) -> tuple[dict[str, Any], str] | None:
    """从原始响应体里截出业务节点，同时返回它的原文。

    验签必须用截出来的原文：反序列化后重新 dumps 会改键序与空白，签名就对不上了。
    """
    marker = f'"{node}"'
    start = raw.find(marker)
    if start < 0:
        return None
    begin = raw.find("{", start + len(marker))
    if begin < 0:
        return None
    depth = 0
    for index in range(begin, len(raw)):
        if raw[index] == "{":
            depth += 1
        elif raw[index] == "}":
            depth -= 1
            if depth == 0:
                body = raw[begin : index + 1]
                return json.loads(body), body
    return None


@dataclass(frozen=True)
class GatewayResponse:
    payload: dict[str, Any]
    signed_text: str | None
    # sign 与业务节点同级，不在节点里面；验签要的是节点原文，两者不能混。
    sign: str | None = None


async def query_trade(credentials: dict[str, str], out_trade_no: str) -> GatewayResponse:
    """查单。自检用它打一个不存在的单号，对账用它确认真实订单。"""
    require(credentials, "alipay", "app_id", "app_private_key", "gateway_url")
    params = common_params(
        app_id=credentials["app_id"],
        method="alipay.trade.query",
        biz_content={"out_trade_no": out_trade_no},
        timestamp=now_stamp(),
    )
    signed = await asyncio.to_thread(sign_params, params, credentials["app_private_key"])
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        response = await client.post(credentials["gateway_url"], data=signed)
    found = extract_node(response.text, "alipay_trade_query_response")
    if found is not None:
        payload, body = found
        return GatewayResponse(payload, body, _top_level_sign(response.text))
    error = extract_node(response.text, "error_response")
    if error is not None:
        payload, _ = error
        return GatewayResponse({**payload, "code": payload.get("code") or "error_response"}, None, None)
    return GatewayResponse({"code": "unparsable", "msg": response.text[:200]}, None, None)


def _top_level_sign(raw: str) -> str | None:
    try:
        value = json.loads(raw).get("sign")
    except json.JSONDecodeError:
        return None
    return str(value) if value else None


async def self_check(credentials: dict[str, str]) -> SelfCheck:
    require(credentials, "alipay", "app_id", "app_private_key", "gateway_url")
    try:
        await asyncio.to_thread(load_private_key, credentials["app_private_key"])
    except ValueError as reason:
        return SelfCheck("local_only", (Check("应用私钥可解析", False, str(reason)[:200]),))
    checks = [Check("应用私钥可解析", True)]
    # 打一个必然不存在的单号：网关受理签名并回"交易不存在"，就同时证明了签名有效、应用可调用，
    # 而且不会产生任何真实交易。
    result = await query_trade(credentials, SELF_CHECK_OUT_TRADE_NO)
    node = result.payload
    code = str(node.get("code") or "")
    sub_code = str(node.get("sub_code") or "")
    detail = f"code={code or '—'} sub_code={sub_code or '—'} {node.get('sub_msg') or node.get('msg') or ''}".strip()
    if code == "10000" or code == "40004":
        checks.append(Check("网关受理签名且应用有效", True, detail))
    elif any(sub_code.startswith(item) for item in PERMISSION_SUB_CODES):
        checks.append(Check("应用未获得该接口权限", False, detail + "；需在开放平台完成产品签约或授权"))
    else:
        checks.append(Check("网关拒绝请求", False, detail))
    if credentials.get("alipay_public_key"):
        ok = bool(result.sign and result.signed_text) and await asyncio.to_thread(
            verify, credentials["alipay_public_key"], str(result.signed_text), str(result.sign)
        )
        checks.append(
            Check("响应验签", ok, "已用支付宝公钥校验响应原文" if ok else "响应缺少签名或公钥不匹配")
        )
    return SelfCheck("live", tuple(checks))
