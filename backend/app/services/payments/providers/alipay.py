import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

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
    yuan_to_cent,
)

# 支付宝按 "yyyy-MM-dd HH:mm:ss" 校验 timestamp 与服务器时差，签名串里必须用东八区时间；
# 直接取本机时间会在部署到非 CST 机器时报 Invalid signature，所以显式钉住时区。
CHINA_TZ = ZoneInfo("Asia/Shanghai")
SELF_CHECK_OUT_TRADE_NO = "KFSELFCHECK00000000000000000"
# 交易关闭只有 TRADE_CLOSED，TRADE_SUCCESS 与 TRADE_FINISHED 都算钱已到账。
TRADE_PAID_STATUSES = frozenset({"TRADE_SUCCESS", "TRADE_FINISHED"})
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
        # biz_content 必须是纯 ASCII：签名算在 UTF-8 字节上，而网关按 charset 解表单时
        # 对非 ASCII 的还原与我们签的那串字节不一致，中文商品名会直接吃 isv.invalid-signature。
        # 转义成 \uXXXX 后两边看到的是同一串字符，网关解出来的 subject 仍然是中文（实测 code=10000）。
        "biz_content": json.dumps(biz_content, ensure_ascii=True, separators=(",", ":")),
    }
    if notify_url:
        params["notify_url"] = notify_url
    if return_url:
        params["return_url"] = return_url
    return params


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


async def execute(
    credentials: dict[str, str],
    method: str,
    biz_content: dict[str, Any],
    *,
    node: str,
    notify_url: str | None = None,
    return_url: str | None = None,
) -> GatewayResponse:
    """签名、发请求、按业务节点截原文。下单与查单走同一条路，只差方法名与节点名。"""
    require(credentials, "alipay", "app_id", "app_private_key", "gateway_url")
    params = common_params(
        app_id=credentials["app_id"],
        method=method,
        biz_content=biz_content,
        timestamp=now_stamp(),
        notify_url=notify_url,
        return_url=return_url,
    )
    signed = await asyncio.to_thread(sign_params, params, credentials["app_private_key"])
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        response = await client.post(credentials["gateway_url"], data=signed)
    found = extract_node(response.text, node)
    if found is not None:
        payload, body = found
        return GatewayResponse(payload, body, _top_level_sign(response.text))
    error = extract_node(response.text, "error_response")
    if error is not None:
        payload, _ = error
        return GatewayResponse({**payload, "code": payload.get("code") or "error_response"}, None, None)
    return GatewayResponse({"code": "unparsable", "msg": response.text[:200]}, None, None)


async def query_trade(credentials: dict[str, str], out_trade_no: str) -> GatewayResponse:
    """查单。自检用它打一个不存在的单号，对账用它确认真实订单。"""
    return await execute(
        credentials, "alipay.trade.query", {"out_trade_no": out_trade_no}, node="alipay_trade_query_response"
    )


def _top_level_sign(raw: str) -> str | None:
    try:
        value = json.loads(raw).get("sign")
    except json.JSONDecodeError:
        return None
    return str(value) if value else None


def verification_key(credentials: dict[str, str]) -> str | None:
    """验签要用的支付宝侧公钥：公钥模式取配置的公钥，证书模式从支付宝公钥证书里抽公钥。

    两种模式共用一条路径，避免"没配公钥就静默跳过验签"。
    """
    if credentials.get("alipay_public_key"):
        return credentials["alipay_public_key"]
    certificate = credentials.get("alipay_public_cert")
    if not certificate:
        return None
    public_key = x509.load_pem_x509_certificate(certificate.encode()).public_key()
    return public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def cent_to_yuan(amount_cent: int) -> str:
    """支付宝金额单位是元、且要求两位小数字符串；用整除拼串，不走 float。"""
    return f"{amount_cent // 100}.{amount_cent % 100:02d}"


async def create_order(credentials: dict[str, str], order: OrderRequest) -> OrderTicket:
    """`alipay.trade.precreate` 预下单，返回 `qr_code` 串给门户渲染成二维码。

    选它而不是 `alipay.trade.page.pay`：门户要的是"在本页扫码付"，跳转式会让用户离开结算页，
    回来时订单状态还没确认，看起来像没付。代价是应用必须签约当面付，没签约时厂商会回
    `isv.insufficient-isv-permissions`，那条 sub_code 由自检原样摊给运营，不做静默回落。
    """
    result = await execute(
        credentials,
        "alipay.trade.precreate",
        {
            "out_trade_no": order.out_trade_no,
            "total_amount": cent_to_yuan(order.amount_cent),
            "subject": order.subject[:256],
        },
        node="alipay_trade_precreate_response",
        notify_url=order.notify_url,
    )
    node = result.payload
    code = str(node.get("code") or "")
    if code != "10000":
        detail = f"code={code or '—'} sub_code={node.get('sub_code') or '—'} {node.get('sub_msg') or node.get('msg') or ''}".strip()
        raise ProviderError(f"支付宝下单失败：{detail[:220]}")
    qr_code = node.get("qr_code")
    if not qr_code:
        raise ProviderError("支付宝下单响应里没有 qr_code")
    return OrderTicket(code_url=str(qr_code))


async def query_order(credentials: dict[str, str], out_trade_no: str) -> PaidState:
    """查单结论只在响应验签通过后才算数：未验签的报文不能拿来记账。"""
    result = await query_trade(credentials, out_trade_no)
    node = result.payload
    code = str(node.get("code") or "")
    detail = f"code={code or '—'} {node.get('sub_msg') or node.get('msg') or ''}".strip()
    if code != "10000":
        return PaidState(False, detail=detail)
    key_material = verification_key(credentials)
    if not key_material:
        return PaidState(False, detail=detail + "；未配支付宝公钥或支付宝公钥证书，无法确认响应真伪")
    if not result.sign or not result.signed_text:
        return PaidState(False, detail=detail + "；响应里没有签名")
    verified = await asyncio.to_thread(verify, key_material, result.signed_text, result.sign)
    if not verified:
        return PaidState(False, detail=detail + "；响应验签失败，按未支付处理")
    status = str(node.get("trade_status") or "")
    return PaidState(
        paid=status in TRADE_PAID_STATUSES,
        amount_cent=yuan_to_cent(node.get("buyer_pay_amount") or node.get("total_amount")),
        provider_trade_no=node.get("trade_no"),
        detail=f"trade_status={status or '—'} " + detail,
    )


def parse_notify(credentials: dict[str, str], notify: NotifyRequest) -> NotifyResult:
    """异步通知验签。待签名串用的是表单原始字段，先解析成模型再重编码会把签名打散。"""
    key_material = verification_key(credentials)
    if not key_material:
        raise ProviderError("未配置支付宝公钥或支付宝公钥证书，无法校验回调签名")
    if not verify(key_material, notify_sign_content(notify.form), str(notify.form.get("sign") or "")):
        raise ProviderError("回调验签失败")
    status = str(notify.form.get("trade_status") or "")
    return NotifyResult(
        out_trade_no=str(notify.form.get("out_trade_no") or ""),
        paid=status in TRADE_PAID_STATUSES,
        amount_cent=yuan_to_cent(notify.form.get("buyer_pay_amount") or notify.form.get("total_amount")),
        provider_trade_no=notify.form.get("trade_no"),
        digest=hashlib.sha256(notify.raw_body).hexdigest(),
        detail=f"trade_status={status or '—'}",
    )


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
    key_material = verification_key(credentials)
    if key_material is None:
        checks.append(
            Check(
                "支付宝侧验签公钥",
                False,
                "公钥模式请填「支付宝公钥」，证书模式请填「支付宝公钥证书」，否则响应和回调都无法校验",
            )
        )
    else:
        source = "支付宝公钥" if credentials.get("alipay_public_key") else "支付宝公钥证书"
        ok = bool(result.sign and result.signed_text) and await asyncio.to_thread(
            verify, key_material, str(result.signed_text), str(result.sign)
        )
        checks.append(
            Check(
                "响应验签",
                ok,
                f"已用{source}校验响应原文"
                if ok
                else f"{source}与网关签名不匹配：公钥模式要拷控制台的「支付宝公钥」（不是应用公钥）；"
                "应用若走公钥证书模式，则改填支付宝公钥证书",
            )
        )
    return SelfCheck("live", tuple(checks))
