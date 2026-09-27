import base64
import binascii
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

# 支付宝网关只有这两个合法取值，让人手输只会得到"签名对了但地址打错"这种查半天的坑。
ALIPAY_GATEWAY_PRODUCTION: Final = "https://openapi.alipay.com/gateway.do"
ALIPAY_GATEWAY_SANDBOX: Final = "https://openapi-sandbox.dl.alipaydev.com/gateway.do"


@dataclass(frozen=True)
class CredentialField:
    key: str
    label: str
    format: str
    max_length: int
    help: str = ""
    secret: bool = False
    required: bool = True
    min_length: int = 1
    default: str | None = None
    editable: bool = True
    options: tuple[str, ...] = ()


def _length(value: str, field: CredentialField) -> str:
    if len(value) > field.max_length:
        raise ValueError(f"{field.label}最长 {field.max_length} 个字符")
    if len(value) < field.min_length:
        raise ValueError(f"{field.label}至少需要 {field.min_length} 个字符")
    return value


def _pattern(pattern: str, message: str) -> Callable[[str, CredentialField], str]:
    def check(value: str, field: CredentialField) -> str:
        if not re.fullmatch(pattern, value):
            raise ValueError(message)
        return _length(value, field)

    return check


def _https_url(value: str, field: CredentialField) -> str:
    if not value.startswith("https://"):
        raise ValueError(f"{field.label}必须是 https 开头的公网地址")
    return _length(value, field)


def _any_url(value: str, field: CredentialField) -> str:
    if not value.startswith(("https://", "http://")):
        raise ValueError(f"{field.label}必须是完整地址")
    return _length(value, field)


def _strip_pem_body(value: str) -> str:
    """控制台给的密钥常常是裸 base64（没有 PEM 头尾），统一成一种形态再解析。"""
    return re.sub(r"\s+", "", value)


def _private_key(value: str, field: CredentialField) -> str:
    try:
        if value.startswith("-----BEGIN"):
            key = serialization.load_pem_private_key(value.encode(), password=None)
        else:
            body = _strip_pem_body(value)
            key = serialization.load_der_private_key(base64.b64decode(body), password=None)
    except (ValueError, TypeError, binascii.Error) as reason:
        raise ValueError(f"{field.label}无法解析：{_brief(reason)}") from reason
    if isinstance(key, rsa.RSAPrivateKey) and key.key_size < 2048:
        raise ValueError(f"{field.label}的 RSA 长度不足 2048 位，厂商会拒绝")
    if not isinstance(key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
        raise ValueError(f"{field.label}必须是 RSA 或 EC 私钥")
    return _length(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
        field,
    )


def _public_key(value: str, field: CredentialField) -> str:
    try:
        if value.startswith("-----BEGIN"):
            key = serialization.load_pem_public_key(value.encode())
        else:
            body = _strip_pem_body(value)
            key = serialization.load_der_public_key(base64.b64decode(body))
    except (ValueError, TypeError, binascii.Error, UnsupportedAlgorithm) as reason:
        raise ValueError(f"{field.label}无法解析：{_brief(reason)}") from reason
    return _length(
        key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode(),
        field,
    )


def _certificate(value: str, field: CredentialField) -> str:
    try:
        x509.load_pem_x509_certificate(value.encode())
    except ValueError as reason:
        raise ValueError(f"{field.label}不是有效的 PEM 证书：{_brief(reason)}") from reason
    return _length(value, field)


def _fixed_bytes(size: int) -> Callable[[str, CredentialField], str]:
    def check(value: str, field: CredentialField) -> str:
        try:
            decoded = base64.b64decode(_strip_pem_body(value), validate=True)
        except (binascii.Error, ValueError) as reason:
            raise ValueError(f"{field.label}不是合法 base64：{_brief(reason)}") from reason
        if len(decoded) != size:
            raise ValueError(f"{field.label}必须是 {size} 字节 base64，当前解出 {len(decoded)} 字节")
        return _length(value, field)

    return check


def _brief(reason: Exception) -> str:
    return str(reason).splitlines()[0][:120] or type(reason).__name__


def _choice(value: str, field: CredentialField) -> str:
    if value not in field.options:
        raise ValueError(f"{field.label}只能是 {' 或 '.join(field.options)}")
    return value


VALIDATORS: Final[dict[str, Callable[[str, CredentialField], str]]] = {
    "text": _length,
    "url": _any_url,
    "https_url": _https_url,
    "choice": _choice,
    "digits": _pattern(r"\d+", "只能填数字"),
    "hex_upper": _pattern(r"[0-9a-fA-F]+", "只能填十六进制字符"),
    "alipay_app_id": _pattern(r"\d{10,20}", "支付宝 APPID 是 10-20 位数字"),
    "wechat_app_id": _pattern(r"wx[0-9a-fA-F]{16}", "微信 AppID 形如 wx 加 16 位十六进制"),
    "mch_id": _pattern(r"\d{8,12}", "微信商户号是 8-12 位数字"),
    "apiv3_key": _pattern(r"[\x21-\x7e]{32}", "APIv3 密钥必须恰好 32 个可见字符"),
    "wechat_serial": _pattern(r"[0-9a-fA-F]{8,64}", "商户API证书序列号是 8-64 位十六进制"),
    "wechat_pub_key_id": _pattern(r"PUB_KEY_ID_[0-9A-Z]{8,64}", "微信支付公钥ID 以 PUB_KEY_ID_ 开头"),
    "stripe_secret_key": _pattern(
        r"s[kw]_(?:test|live)_[A-Za-z0-9]{10,}", "Stripe Secret Key 以 sk_test_/sk_live_ 开头"
    ),
    "stripe_whsec_key": _pattern(r"whsec_[A-Za-z0-9]{10,}", "Stripe Webhook 签名密钥以 whsec_ 开头"),
    "private_key": _private_key,
    "public_key": _public_key,
    "certificate": _certificate,
    "base64_16": _fixed_bytes(16),
}

# 迁移前遗留列的展示名：库里可能有，但绝不因为"不在 spec 里"就把它藏起来。
LEGACY_LABELS: Final[dict[str, str]] = {
    "legacy_secret": "历史密钥（旧版单密钥）",
    "legacy_merchant_id": "历史商户标识（旧版）",
}

CREDENTIAL_SPECS: Final[dict[str, list[CredentialField]]] = {
    "alipay": [
        CredentialField(
            "app_id",
            "应用 APPID",
            "alipay_app_id",
            32,
            help="开放平台控制台的应用 APPID，正式与沙箱是两套，不要混填。",
        ),
        CredentialField(
            "gateway_url",
            "网关地址",
            "choice",
            120,
            help="选错网关的表现是签名有效却查不到应用，所以只允许下拉。",
            default=ALIPAY_GATEWAY_PRODUCTION,
            options=(ALIPAY_GATEWAY_PRODUCTION, ALIPAY_GATEWAY_SANDBOX),
            required=False,
        ),
        CredentialField(
            "app_private_key",
            "应用私钥",
            "private_key",
            8000,
            secret=True,
            help="RSA2 应用私钥。控制台给的裸 base64 和 PEM 两种形态都能粘，保存时统一成 PEM。",
        ),
        CredentialField(
            "alipay_public_key",
            "支付宝公钥",
            "public_key",
            8000,
            help="公钥模式必填；用证书模式时留空，验签改用支付宝公钥证书。",
            required=False,
        ),
        CredentialField(
            "notify_url",
            "异步通知地址",
            "url",
            300,
            help="厂商回调打到这里，必须公网可达；留空则只能靠主动查单入账。",
            required=False,
        ),
        CredentialField(
            "return_url", "同步跳转地址", "url", 300, help="付款完成后浏览器跳回的前端地址。", required=False
        ),
        CredentialField(
            "sign_type",
            "签名算法",
            "choice",
            10,
            help="支付宝只接受 RSA2，MD5/RSA1 都已下线，不给选错的口子。",
            default="RSA2",
            editable=False,
            options=("RSA2",),
        ),
        CredentialField(
            "charset",
            "字符集",
            "choice",
            10,
            help="与厂商请求参数保持一致，全站固定 UTF-8。",
            default="UTF-8",
            editable=False,
            options=("UTF-8",),
        ),
        CredentialField(
            "app_cert",
            "应用公钥证书",
            "certificate",
            20000,
            required=False,
            help="证书模式三项之一；填了服务端自动算出 app_cert_sn，不需要手填。",
        ),
        CredentialField(
            "alipay_public_cert",
            "支付宝公钥证书",
            "certificate",
            20000,
            required=False,
            help="证书模式验签用这把。",
        ),
        CredentialField("alipay_root_cert", "支付宝根证书", "certificate", 20000, required=False),
        CredentialField(
            "content_encrypt_key",
            "内容加密 AES 密钥",
            "base64_16",
            64,
            secret=True,
            required=False,
            help="只有接口开了 AES 加密才需要填。",
        ),
    ],
    "wechat": [
        CredentialField("mch_id", "商户号", "mch_id", 12, help="微信支付商户平台分配的商户号。"),
        CredentialField(
            "app_id",
            "AppID",
            "wechat_app_id",
            32,
            help="公众号/小程序/开放平台 AppID，必须已在商户平台与该商户号绑定。",
        ),
        CredentialField(
            "apiv3_key",
            "APIv3 密钥",
            "apiv3_key",
            32,
            secret=True,
            help="商户平台设置的 32 位 APIv3 密钥，用来解密回调里的 resource。",
        ),
        CredentialField(
            "merchant_private_key",
            "商户API私钥",
            "private_key",
            8000,
            secret=True,
            help="apiclient_key.pem 全文，每个 APIv3 请求都用它签名。",
        ),
        CredentialField(
            "cert_serial_no",
            "商户API证书序列号",
            "wechat_serial",
            64,
            help="apiclient_cert.pem 的序列号，请求头 Wechatpay-Serial 用它。",
        ),
        CredentialField(
            "notify_url",
            "支付回调地址",
            "https_url",
            300,
            help="微信强制 https 公网地址，http 隧道会被直接拒；留空则只能靠主动查单入账。",
            required=False,
        ),
        CredentialField(
            "wechat_pay_public_key",
            "微信支付公钥",
            "public_key",
            8000,
            required=False,
            help="新商户用公钥模式时填这项；有平台证书的商户留空，系统自己拉取。",
        ),
        CredentialField(
            "wechat_pay_public_key_id",
            "微信支付公钥ID",
            "wechat_pub_key_id",
            64,
            required=False,
            help="形如 PUB_KEY_ID_xxx，与上面的公钥配对，回调验签时按它选公钥。",
        ),
        CredentialField(
            "platform_cert",
            "微信支付平台证书",
            "certificate",
            20000,
            required=False,
            help="留空即可：连通性自检会调 /v3/certificates 自动拉取并用 APIv3 密钥解密保存。",
        ),
    ],
    "stripe": [
        CredentialField(
            "secret_key",
            "Secret Key",
            "stripe_secret_key",
            200,
            secret=True,
            help="sk_test_ 或 sk_live_；环境以密钥前缀为准，不另设开关，避免两处状态互相矛盾。",
        ),
        CredentialField(
            "webhook_signing_secret",
            "Webhook 签名密钥",
            "stripe_whsec_key",
            200,
            secret=True,
            help="Dashboard 里该 endpoint 的 whsec_；没有它回调无法验签。",
        ),
        CredentialField("publishable_key", "Publishable Key", "text", 200, required=False),
        CredentialField(
            "api_version",
            "API 版本",
            "text",
            40,
            required=False,
            help="建议固定，留空走 Stripe 账户默认版本。",
        ),
        CredentialField("success_url", "支付成功跳转地址", "url", 300, required=False),
        CredentialField("cancel_url", "支付取消跳转地址", "url", 300, required=False),
    ],
    # 线下对公之类：能出现在渠道列表和流水里，但不出凭据表单、不可在线下单。
    "custom": [],
}

# 公钥模式与证书模式二选一的组合约束：只配一半比不配更危险，因为要打到厂商侧才暴露。
ALIPAY_KEY_PAIR_MESSAGE: Final = "支付宝公钥与支付宝公钥证书至少要配一项"
WECHAT_KEY_PAIR_MESSAGE: Final = "微信支付公钥（含公钥ID）与平台证书至少要配一项"

CHANNEL_LABELS: Final[dict[str, str]] = {
    "alipay": "支付宝",
    "wechat": "微信支付",
    "stripe": "Stripe",
    "custom": "线下/自定义",
}
CHANNEL_TYPES: Final[tuple[str, ...]] = tuple(CREDENTIAL_SPECS)
PAYABLE_TYPES: Final[tuple[str, ...]] = ("alipay", "wechat", "stripe")


def spec_for(channel_type: str) -> list[CredentialField]:
    try:
        return CREDENTIAL_SPECS[channel_type]
    except KeyError as reason:
        raise ValueError(f"未知的渠道类型：{channel_type}") from reason


def field_map(channel_type: str) -> dict[str, CredentialField]:
    return {field.key: field for field in spec_for(channel_type)}


def required_keys(channel_type: str) -> tuple[str, ...]:
    """需要人来配的项。固定项（签名算法、字符集）也算"必须有值"，但不是人该操心的事，所以不列进来。"""
    return tuple(field.key for field in spec_for(channel_type) if field.required and field.editable)


def missing_required(channel_type: str, present: Iterable[str]) -> list[str]:
    """返回缺失字段的中文名，直接进状态徽章，让人知道差哪几项。"""
    have = set(present)
    fields = field_map(channel_type)
    return [fields[key].label for key in required_keys(channel_type) if key not in have]


def with_defaults(channel_type: str, stored: Mapping[str, str]) -> dict[str, str]:
    """固定项（签名算法、字符集）由规格决定而不是由库里存了什么决定，缺了也能安全使用。"""
    values = {field.key: field.default for field in spec_for(channel_type) if field.default is not None}
    values.update(stored)
    return values


def configuration_error(channel_type: str, present: Iterable[str]) -> str | None:
    """必填项之外的组合约束：只配了一半的"两种模式"比没配更危险，因为下单会打到厂商才报错。"""
    have = set(present)
    if channel_type == "alipay" and not {"alipay_public_key", "alipay_public_cert"} & have:
        return ALIPAY_KEY_PAIR_MESSAGE
    if channel_type == "wechat" and "platform_cert" not in have:
        if not {"wechat_pay_public_key", "wechat_pay_public_key_id"} <= have:
            return WECHAT_KEY_PAIR_MESSAGE
    return None


def describe(channel_type: str, key: str) -> tuple[str, bool]:
    """配置项的中文名与"是否密钥"。历史遗留键也走这里，保证迁移后的旧值仍能被列出来。"""
    field = field_map(channel_type).get(key)
    if field is not None:
        return field.label, field.secret
    return LEGACY_LABELS.get(key, key), key == "legacy_secret"


def validate_value(channel_type: str, key: str, value: str) -> str:
    """单项校验。迁移回填和"只改一个字段"的路径都要用它，避免为了验一项而构造整份输入。"""
    fields = field_map(channel_type)
    if key not in fields:
        raise ValueError(f"{channel_type} 渠道没有这个配置项：{key}")
    return _validate(fields[key], value)


def normalize_input(channel_type: str, values: Mapping[str, str | None]) -> dict[str, str]:
    """校验调用方给出的键并返回规范化后的值（加密前形态）。

    值为 None 或空串表示"清除"，不会出现在返回里，由调用方单独处理；未知键一律拒绝，
    因为前端拼错键名的后果是静默不保存，比报错难查得多。
    """
    fields = field_map(channel_type)
    result: dict[str, str] = {}
    for key, value in values.items():
        if key not in fields:
            if key in LEGACY_LABELS:
                if value and value.strip():
                    result[key] = value.strip()[:8000]
                continue
            raise ValueError(f"{channel_type} 渠道没有这个配置项：{key}")
        field = fields[key]
        if value is None or not value.strip():
            continue
        result[key] = field.default if not field.editable and field.default else _validate(field, value)
    for field in fields.values():
        if not field.editable and field.default and field.key not in values:
            result[field.key] = field.default
    return result


def _validate(field: CredentialField, value: str) -> str:
    try:
        validator = VALIDATORS[field.format]
    except KeyError as reason:
        raise ValueError(f"配置项 {field.key} 声明了未知校验器 {field.format}") from reason
    return validator(value.strip(), field)


def spec_view(channel_type: str) -> dict[str, Any]:
    """给管理端渲染表单用的元数据：字段名、标签、提示、是否密钥、是否必填全在这里，前端不再抄一份。"""
    return {
        "channel_type": channel_type,
        "display": CHANNEL_LABELS[channel_type],
        "payable": channel_type in PAYABLE_TYPES,
        "fields": [
            {
                "key": field.key,
                "label": field.label,
                "help": field.help,
                "secret": field.secret,
                "required": field.required and field.editable,
                "editable": field.editable,
                "default": field.default,
                "options": list(field.options),
                "max_length": field.max_length,
                "multiline": field.format in {"private_key", "public_key", "certificate"},
            }
            for field in spec_for(channel_type)
        ],
    }
