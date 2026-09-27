from dataclasses import dataclass
from typing import Any

from app.services.payments import specs

CHECK_TIMEOUT_SECONDS = 12


class ProviderError(Exception):
    """厂商侧的失败原因，直接进自检结果，不把它伪装成"配置无效"。"""


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class SelfCheck:
    """mode 只允许 live / local_only：live 表示真打过厂商，local_only 表示只证明了本地自洽。

    stores 装自检过程中顺手取回、应当回存成凭据的内容（微信用它缓存平台证书）。
    """

    mode: str
    checks: tuple[Check, ...]
    stores: dict[str, str] | None = None

    def view(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "checks": [{"name": item.name, "ok": item.ok, "detail": item.detail} for item in self.checks],
        }

    @property
    def passed(self) -> bool:
        return all(item.ok for item in self.checks)


@dataclass(frozen=True)
class OrderRequest:
    """下单要传给厂商的最小事实。金额一律用分，与账本同一口径；跨币种换汇本轮不做。"""

    out_trade_no: str
    amount_cent: int
    subject: str
    notify_url: str | None = None
    return_url: str | None = None


@dataclass(frozen=True)
class OrderTicket:
    """厂商给的"怎么去付"。两种形态互斥：扫码串或跳转地址。"""

    code_url: str | None = None
    redirect_url: str | None = None
    provider_trade_no: str | None = None

    def view(self) -> dict[str, Any]:
        return {"code_url": self.code_url, "redirect_url": self.redirect_url}


@dataclass(frozen=True)
class PaidState:
    """查单结论。amount_cent 是厂商侧实收金额，入账前必须与订单金额对得上。"""

    paid: bool
    amount_cent: int | None = None
    provider_trade_no: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class NotifyResult:
    """厂商回调解密后的事实。digest 是原始报文的指纹，落库用于去重和留证。"""

    out_trade_no: str
    paid: bool
    amount_cent: int | None = None
    provider_trade_no: str | None = None
    digest: str = ""
    detail: str = ""


@dataclass(frozen=True)
class NotifyRequest:
    """回调的原始材料。三家要的东西不同（微信看响应头、支付宝看表单项、Stripe 看单个头），
    统一装进一个对象，路由层就不必为每家记一套参数顺序。
    """

    headers: dict[str, str]
    raw_body: bytes
    form: dict[str, str]


def yuan_to_cent(value: object) -> int | None:
    """支付宝金额单位是元（字符串），换成分；不用 float 以免出现 0.1+0.2 那类误差。

    畸形值一律返回 None 让调用方按"金额对不上"处理，不能抛出去把整条回调打成 500。
    """
    text = str(value or "").strip()
    if not text:
        return None
    units, _, fraction = text.partition(".")
    if not units.isdigit() or (fraction and not fraction.isdecimal()):
        return None
    cents = (fraction + "00")[:2]
    return int(units) * 100 + int(cents)


def require(credentials: dict[str, str], channel_type: str, *keys: str) -> None:
    """缺凭据时报中文名：让运营看得懂缺哪一项，而不是去猜 mch_id 是什么。"""
    missing = [key for key in keys if not credentials.get(key)]
    if missing:
        labels = "、".join(specs.describe(channel_type, key)[0] for key in missing)
        raise ProviderError("缺少配置项：" + labels)
