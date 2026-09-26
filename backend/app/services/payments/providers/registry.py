from collections.abc import Awaitable, Callable

from app.services.payments.providers import alipay, base, stripe, wechat

SELF_CHECKS: dict[str, Callable[[dict[str, str]], Awaitable[base.SelfCheck]]] = {
    "alipay": alipay.self_check,
    "wechat": wechat.self_check,
    "stripe": stripe.self_check,
}


async def run_self_check(channel_type: str, credentials: dict[str, str]) -> base.SelfCheck:
    handler = SELF_CHECKS.get(channel_type)
    if handler is None:
        raise base.ProviderError("该渠道类型不支持公众号在线下单，无需自检")
    return await handler(credentials)
