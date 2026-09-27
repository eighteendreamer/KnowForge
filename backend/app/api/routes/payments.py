"""门户在线下单与厂商回调。

下单先落库再打厂商：反过来的话中途崩溃会留下一笔"厂商知道、我们不知道"的订单，
用户付的钱就成了查无此单的损失。落库后的"created"不是已支付，只是"这单我们认得"。
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PortalAccount, Session
from app.api.routes.recharge import master_key
from app.core.config import Settings
from app.core.errors import AppError, success
from app.models import PaymentOrder, RechargeChannel, RechargePackage
from app.schemas.billing import OrderInput, PromoQuoteInput
from app.services import billing
from app.services.payments import credentials as channel_credentials
from app.services.payments import promo, settlement
from app.services.payments.providers import registry
from app.services.payments.providers.base import NotifyRequest, OrderRequest, ProviderError

router = APIRouter(prefix="/v1/portal", tags=["门户在线充值"])
notify_router = APIRouter(prefix="/v1/payments/notify", tags=["支付回调"])

ORDER_TTL = timedelta(minutes=30)
# 一个账号挂太多待支付订单会让厂商侧积压，也给刷单留出空间；30 分钟内最多 5 张。
MAX_OPEN_ORDERS = 5
# 主动查单要花钱也花连接，给一张订单一个硬上限，超了就等回调或人工。
MAX_SYNC_ATTEMPTS = 40
OPEN_STATUSES = ("created", "pending")


def out_trade_no() -> str:
    """商户单号。时间前缀方便人工查单，随机后缀保证同秒内不撞；它也是幂等锚点，必须全局唯一。"""
    return "KF" + datetime.now(UTC).strftime("%Y%m%d%H%M%S") + secrets.token_hex(5).upper()


def _redirects(settings: Settings, code: str, values: dict[str, str]) -> tuple[str | None, str | None]:
    """回调与跳回地址以渠道里配的用厂商回调地址优先，其次按站点基址拼出来。"""
    notify = values.get("notify_url") or (
        f"{settings.site_url}/v1/payments/notify/{code}" if settings.site_url else ""
    )
    landing = values.get("return_url") or (f"{settings.site_url}/recharge" if settings.site_url else "")
    return notify or None, landing or None


async def payable_channel(session: AsyncSession, code: str) -> RechargeChannel:
    channel = await session.scalar(select(RechargeChannel).where(RechargeChannel.code == code))
    if channel is None:
        raise AppError(404, 1001, "支付渠道不存在")
    if not channel.enabled:
        raise AppError(409, 4001, "该渠道尚未开通")
    if not registry.supports_orders(channel.channel_type):
        raise AppError(409, 4001, "该渠道不支持公众号在线下单，请联系平台在后台入账")
    return channel


async def _owned(session: AsyncSession, reference: str, account_id: int) -> PaymentOrder:
    order = await session.scalar(select(PaymentOrder).where(PaymentOrder.out_trade_no == reference))
    if order is None or order.account_id != account_id:
        # 别人的单号一律报"不存在"：单号可枚举时不能泄露"这单确实有，只是不归你"。
        raise AppError(404, 1001, "订单不存在")
    return order


async def _view(session: AsyncSession, order: PaymentOrder) -> dict[str, Any]:
    channel = await session.get(RechargeChannel, order.channel_id)
    return billing.order_view(order, channel.display_name if channel else "")


async def _reject_provider(session: AsyncSession, order: PaymentOrder, detail: str) -> None:
    order.status = "failed"
    order.closed_at = datetime.now(UTC)
    await settlement.record_event(session, order.id, "provider_rejected", {"detail": detail[:300]})
    await session.commit()


@router.post("/promo/quote")
async def quote_promo(body: PromoQuoteInput, session: Session, user: PortalAccount):
    """结算页点"应用"时算一次价。只读不写：促销额度在订单真的付掉时才占用。"""
    package = await session.get(RechargePackage, body.package_id)
    if package is None or not package.enabled:
        raise AppError(404, 1001, "充值档位不存在或已下架")
    result = await promo.quote(session, package, body.promo_code, user.id, now=datetime.now(UTC))
    return success(result.view())


@router.post("/orders")
async def create_order(body: OrderInput, request: Request, session: Session, user: PortalAccount):
    settings = request.app.state.settings
    master = master_key(request)
    channel = await payable_channel(session, body.channel_code)
    package = await session.get(RechargePackage, body.package_id)
    if package is None or not package.enabled:
        raise AppError(404, 1001, "充值档位不存在或已下架")
    # 报价在结算页算过一次，这里重算一遍：客户端传来的任何金额都不进这条链路。
    priced = await promo.quote(session, package, body.promo_code, user.id, now=datetime.now(UTC))
    if body.promo_code.strip() and not priced.applied:
        raise AppError(400, 1001, priced.reason or "促销码不可用")
    now = datetime.now(UTC)
    open_count = await session.scalar(
        select(func.count())
        .select_from(PaymentOrder)
        .where(
            PaymentOrder.account_id == user.id,
            PaymentOrder.status.in_(OPEN_STATUSES),
            PaymentOrder.expires_at > now,
        )
    )
    if (open_count or 0) >= MAX_OPEN_ORDERS:
        raise AppError(429, 4002, "待支付订单已达上限，请先完成支付或等旧订单超时")

    order = PaymentOrder(
        out_trade_no=out_trade_no(),
        account_id=user.id,
        channel_id=channel.id,
        package_id=package.id,
        amount_cent=priced.amount_cent,
        bonus_cent=priced.bonus_cent,
        payable_cent=priced.payable_cent,
        discount_cent=priced.discount_cent,
        promo_code_id=priced.code_id,
        status="created",
        expires_at=now + ORDER_TTL,
    )
    session.add(order)
    # 事件行按 order_id 外键引用，得先 flush 拿到主键，不能等 commit。
    await session.flush()
    await settlement.record_event(
        session,
        order.id,
        "order_created",
        {"channel": channel.channel_type, "discount_cent": order.discount_cent},
    )
    await session.commit()

    values = await channel_credentials.load_values(session, master, channel.id, include_secrets=True)
    notify_url, return_url = _redirects(settings, channel.code, values)
    # 打厂商之前结束事务：跨远程调用持有池化连接会把整个后端拖死。
    await session.commit()
    request_payload = OrderRequest(
        out_trade_no=order.out_trade_no,
        amount_cent=order.payable_cent,
        subject=f"KnowForge 额度充值 · {package.label}",
        notify_url=notify_url,
        return_url=return_url,
    )
    try:
        ticket = await registry.create_order(channel.channel_type, values, request_payload)
    except ProviderError as reason:
        await _reject_provider(session, order, str(reason))
        raise AppError(502, 5001, f"厂商下单失败：{str(reason)[:200]}") from reason
    order.status = "pending"
    order.code_url = ticket.code_url
    order.redirect_url = ticket.redirect_url
    order.provider_trade_no = ticket.provider_trade_no
    await settlement.record_event(
        session,
        order.id,
        "order_placed",
        {"code_url": bool(ticket.code_url), "redirect_url": bool(ticket.redirect_url)},
    )
    await session.commit()
    return success(await _view(session, order))


@router.get("/orders")
async def list_orders(session: Session, user: PortalAccount, limit: int = Query(20, ge=1, le=50)):
    """最近订单。没付完的单要能回来继续付，否则刷新一下就变成一笔找不回的挂账。"""
    pairs = (
        await session.execute(
            select(PaymentOrder, RechargeChannel.display_name)
            .join(RechargeChannel, RechargeChannel.id == PaymentOrder.channel_id)
            .where(PaymentOrder.account_id == user.id)
            .order_by(PaymentOrder.id.desc())
            .limit(limit)
        )
    ).all()
    return success({"items": [billing.order_view(row, name) for row, name in pairs]})


@router.get("/orders/{reference}")
async def get_order(reference: str, session: Session, user: PortalAccount):
    order = await _owned(session, reference, user.id)
    return success(await _view(session, order))


@router.post("/orders/{reference}/sync")
async def sync_order(reference: str, request: Request, session: Session, user: PortalAccount):
    """主动查单。回调进不来（本地没有 https 公网地址是常态）时，这是唯一的自动入账路径。"""
    order = await _owned(session, reference, user.id)
    if order.status == "paid":
        return success({**(await _view(session, order)), "sync": {"ok": True, "detail": "已入账"}})
    channel = await session.get(RechargeChannel, order.channel_id)
    if channel is None or not registry.supports_orders(channel.channel_type):
        raise AppError(409, 4001, "该渠道不支持公众号在线下单，请联系平台在后台入账")
    if order.attempts >= MAX_SYNC_ATTEMPTS:
        raise AppError(429, 4002, "查单次数已用尽，请联系平台核对这笔支付")
    order.attempts += 1
    master = master_key(request)
    values = await channel_credentials.load_values(session, master, channel.id, include_secrets=True)
    await session.commit()
    try:
        state = await registry.query_order(channel.channel_type, values, order.out_trade_no)
    except ProviderError as reason:
        await session.commit()
        return success({**(await _view(session, order)), "sync": {"ok": False, "detail": str(reason)[:300]}})
    if state.paid:
        result = await settlement.mark_paid_and_credit(
            session,
            order,
            source=channel.code,
            received_cent=state.amount_cent,
            provider_trade_no=state.provider_trade_no,
            note="主动查单确认",
        )
        detail = result.detail
    else:
        detail = state.detail
        if order.expires_at <= datetime.now(UTC):
            order.status = "expired"
            order.closed_at = datetime.now(UTC)
            await settlement.record_event(session, order.id, "expired", {"detail": state.detail[:300]})
    await session.commit()
    return success(
        {
            **(await _view(session, order)),
            "sync": {"ok": state.paid, "detail": detail[:300]},
            "balance_cent": await billing.balance_cent(session, user.id),
        }
    )


@notify_router.post("/{code}")
async def provider_notify(code: str, request: Request, session: Session):
    """厂商异步通知。必须用原始字节验签，所以这里不接 Pydantic 模型。"""
    channel = await session.scalar(select(RechargeChannel).where(RechargeChannel.code == code))
    if channel is None or not registry.supports_orders(channel.channel_type):
        await session.rollback()
        return _reject("", "渠道不存在或未开通")
    try:
        master = master_key(request)
    except AppError:
        # 没有主密钥就解不开凭据、也就验不了签。验不了签绝不能入账，回非 2xx 让厂商重投。
        await session.rollback()
        return _reject(channel.channel_type, "服务未配置支付主密钥", status=503)
    raw_body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}
    form: dict[str, str] = {}
    if headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        form = {key: str(value) for key, value in (await request.form()).items()}
    values = await channel_credentials.load_values(session, master, channel.id, include_secrets=True)
    await session.commit()
    try:
        parsed = await registry.parse_notify(
            channel.channel_type, values, NotifyRequest(headers=headers, raw_body=raw_body, form=form)
        )
    except (ProviderError, ValueError) as reason:
        # 验签不过的报文里那个单号没有被证明可信，连"收到过一条通知"都不该记到某张订单上。
        await session.rollback()
        return _reject(channel.channel_type, str(reason))
    order = await settlement.find_by_out_trade_no(session, parsed.out_trade_no)
    if order is None or order.channel_id != channel.id:
        await session.rollback()
        return _reject(channel.channel_type, "订单不存在")
    await settlement.mark_paid_and_credit(
        session,
        order,
        source=channel.code,
        received_cent=parsed.amount_cent,
        provider_trade_no=parsed.provider_trade_no,
        notify_digest=parsed.digest,
        note=f"回调 {parsed.detail}"[:200],
    )
    await session.commit()
    # credited / duplicate / amount_mismatch 三种结论都算"这条通知我们处理完了"：
    # 金额不一致重投一百次也不会变对，回非 2xx 只会让厂商一直重试。
    return _ack(channel.channel_type)


def _ack(channel_type: str) -> Response:
    """三家认可的"我处理完了"长得不一样，回错文本厂商会一直重投。"""
    if channel_type == "alipay":
        return PlainTextResponse("success")
    if channel_type == "wechat":
        return JSONResponse({}, status_code=200)
    return Response(status_code=200)


def _reject(channel_type: str, detail: str, status: int = 400) -> Response:
    if channel_type == "alipay":
        return PlainTextResponse("failure", status_code=status)
    if channel_type == "wechat":
        return JSONResponse({"code": "FAIL", "message": detail[:200]}, status_code=status)
    return Response(status_code=status)
