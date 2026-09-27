import asyncio
from collections.abc import Awaitable, Callable

from app.services.payments.providers import alipay, base, stripe, wechat

SELF_CHECKS: dict[str, Callable[[dict[str, str]], Awaitable[base.SelfCheck]]] = {
    "alipay": alipay.self_check,
    "wechat": wechat.self_check,
    "stripe": stripe.self_check,
}

ORDER_CREATORS: dict[str, Callable[[dict[str, str], base.OrderRequest], Awaitable[base.OrderTicket]]] = {
    "alipay": alipay.create_order,
    "wechat": wechat.create_order,
    "stripe": stripe.create_order,
}

ORDER_QUERIES: dict[str, Callable[[dict[str, str], str], Awaitable[base.PaidState]]] = {
    "alipay": alipay.query_order,
    "wechat": wechat.query_order,
    "stripe": stripe.query_order,
}

# 签名/验签和证书解析是 CPU 重活，统一在这里 offload，路由层不需要记得逐个包 to_thread。
NOTIFY_PARSERS: dict[str, Callable[[dict[str, str], base.NotifyRequest], base.NotifyResult]] = {
    "alipay": alipay.parse_notify,
    "wechat": wechat.parse_notify,
    "stripe": stripe.parse_notify,
}

NOT_ORDERABLE = "该渠道类型不支持公众号在线下单，只能由平台在后台按账本入账"


def supports_orders(channel_type: str) -> bool:
    return channel_type in ORDER_CREATORS


async def run_self_check(channel_type: str, credentials: dict[str, str]) -> base.SelfCheck:
    handler = SELF_CHECKS.get(channel_type)
    if handler is None:
        raise base.ProviderError(NOT_ORDERABLE)
    return await handler(credentials)


async def create_order(
    channel_type: str, credentials: dict[str, str], order: base.OrderRequest
) -> base.OrderTicket:
    handler = ORDER_CREATORS.get(channel_type)
    if handler is None:
        raise base.ProviderError(NOT_ORDERABLE)
    return await handler(credentials, order)


async def query_order(channel_type: str, credentials: dict[str, str], out_trade_no: str) -> base.PaidState:
    handler = ORDER_QUERIES.get(channel_type)
    if handler is None:
        raise base.ProviderError(NOT_ORDERABLE)
    return await handler(credentials, out_trade_no)


async def parse_notify(
    channel_type: str, credentials: dict[str, str], notify: base.NotifyRequest
) -> base.NotifyResult:
    handler = NOTIFY_PARSERS.get(channel_type)
    if handler is None:
        raise base.ProviderError(NOT_ORDERABLE)
    return await asyncio.to_thread(handler, credentials, notify)
