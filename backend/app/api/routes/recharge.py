from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Query, Request
from pydantic import SecretStr
from sqlalchemy import func, select

from app.api.deps import Session, SuperAdmin
from app.core.errors import AppError, success
from app.models import Account, PaymentOrder, PaymentOrderEvent, RechargeChannel, RechargePackage
from app.schemas.billing import ChannelInput, ChannelPatch, PackageInput
from app.services.audit import record_audit
from app.services.billing import channel_view, order_view, package_view
from app.services.payments import credentials as channel_credentials
from app.services.payments import reconcile, specs
from app.services.payments.providers import registry
from app.services.payments.providers.base import Check, ProviderError, SelfCheck

router = APIRouter(prefix="/v1/admin/recharge", tags=["充值系统管理"])


def master_key(request: Request) -> bytes:
    # 主密钥只来自 .env：它和被加密的凭据同库同源就等于没加密。
    key = request.app.state.settings.payment_master_key_bytes
    if not key:
        raise AppError(
            503,
            5003,
            "未配置支付主密钥：请在 .env 设置 KNOFORGE_PAYMENT_MASTER_KEY 后重启服务",
        )
    return key


def _normalized(channel_type: str, values: Mapping[str, str | None]) -> dict[str, str]:
    try:
        return specs.normalize_input(channel_type, values)
    except ValueError as reason:
        raise AppError(400, 1001, f"支付配置无效：{reason}") from reason


def _split(
    channel_type: str, credentials: Mapping[str, SecretStr | str | None]
) -> tuple[dict[str, str], list[str]]:
    """把三态输入拆成"要写的值"与"要清除的键"；空串和 null 都是清除。

    调用方可能是请求体（SecretStr）也可能是自检回存的明文，两种都要接住。
    """
    plain = {
        key: (
            None if value is None else (value.get_secret_value() if isinstance(value, SecretStr) else value)
        )
        for key, value in credentials.items()
    }
    return _normalized(channel_type, plain), [
        key for key, value in plain.items() if not (value or "").strip()
    ]


def _enable_gate(state: dict[str, Any], enabled: bool) -> None:
    if not enabled or state["complete"]:
        return
    reasons = [str(item) for item in state["missing"]]
    if state["problem"]:
        reasons.append(str(state["problem"]))
    raise AppError(400, 1001, "渠道配置不完整，无法启用：" + "、".join(reasons))


@router.get("/packages")
async def list_packages(session: Session, user: SuperAdmin):
    rows = await session.scalars(
        select(RechargePackage).order_by(RechargePackage.sort_order, RechargePackage.id)
    )
    return success({"items": [package_view(row) for row in rows]})


@router.post("/packages")
async def create_package(body: PackageInput, request: Request, session: Session, user: SuperAdmin):
    row = RechargePackage(**body.model_dump())
    session.add(row)
    await session.flush()
    record_audit(session, request, user, "create", "recharge_package", row.id, {"label": row.label})
    await session.commit()
    return success(package_view(row))


@router.patch("/packages/{package_id}")
async def patch_package(
    package_id: int, body: PackageInput, request: Request, session: Session, user: SuperAdmin
):
    row = await session.get(RechargePackage, package_id, with_for_update=True)
    if row is None:
        raise AppError(404, 1001, "充值套餐不存在")
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    record_audit(session, request, user, "update", "recharge_package", row.id)
    await session.commit()
    # updated_at 由服务端 onupdate 生成，提交后 flush 才是新值；不回读的话视图里的同步属性访问会炸成 MissingGreenlet。
    await session.refresh(row)
    return success(package_view(row))


@router.delete("/packages/{package_id}")
async def delete_package(package_id: int, request: Request, session: Session, user: SuperAdmin):
    row = await session.get(RechargePackage, package_id)
    if row is None:
        raise AppError(404, 1001, "充值套餐不存在")
    await session.delete(row)
    record_audit(session, request, user, "delete", "recharge_package", package_id)
    await session.commit()
    return success({"id": package_id})


@router.get("/credential-specs")
async def credential_specs(session: Session, user: SuperAdmin):
    """管理端表单的唯一字段来源：字段名、标签、提示、是否密钥、是否必填都从这里出。"""
    return success({"types": [specs.spec_view(channel_type) for channel_type in specs.CHANNEL_TYPES]})


@router.get("/channels")
async def list_channels(session: Session, user: SuperAdmin, request: Request):
    rows = await session.scalars(select(RechargeChannel).order_by(RechargeChannel.id))
    views = await channel_credentials.load_all(session, master_key(request))
    return success({"items": [channel_view(row, views.get(row.id, [])) for row in rows]})


@router.post("/channels")
async def create_channel(body: ChannelInput, request: Request, session: Session, user: SuperAdmin):
    master = master_key(request)
    values, _ = _split(body.channel_type, body.credentials)
    state = channel_credentials.state(body.channel_type, set(values))
    _enable_gate(state, body.enabled)
    row = RechargeChannel(
        code=body.code,
        display_name=body.display_name,
        channel_type=body.channel_type,
        enabled=body.enabled,
    )
    session.add(row)
    await session.flush()
    await channel_credentials.apply(session, master, row.id, row.channel_type, values, user.id)
    record_audit(
        session,
        request,
        user,
        "create",
        "recharge_channel",
        row.id,
        {
            "code": row.code,
            "channel_type": row.channel_type,
            "fields": [f"credentials.{key}" for key in values],
        },
    )
    await session.commit()
    views = await channel_credentials.load_all(session, master)
    return success(channel_view(row, views.get(row.id, [])))


@router.patch("/channels/{channel_id}")
async def patch_channel(
    channel_id: int, body: ChannelPatch, request: Request, session: Session, user: SuperAdmin
):
    master = master_key(request)
    row = await session.get(RechargeChannel, channel_id, with_for_update=True)
    if row is None:
        raise AppError(404, 1001, "支付渠道不存在")
    changed: list[str] = []
    if body.display_name is not None:
        row.display_name = body.display_name
        changed.append("display_name")
    credentials = body.credentials or {}
    values, cleared = _split(row.channel_type, credentials)
    applied = await channel_credentials.apply(
        session,
        master,
        row.id,
        row.channel_type,
        {**values, **{key: None for key in cleared}},
        user.id,
    )
    changed.extend(f"credentials.{key}" for key in applied["rotated"] + applied["cleared"])
    if applied["rotated"] or applied["cleared"]:
        # 换过任何一项凭据，上一次的自检结论就作废了：留着"通过"会掩盖新密钥根本没验过这件事。
        row.verified_at = None
        row.verify_passed = None
        row.verify_detail = None
    if body.enabled is not None:
        views = await channel_credentials.load_all(session, master)
        _enable_gate(channel_credentials.state(row.channel_type, _keys(views.get(row.id, []))), body.enabled)
        row.enabled = body.enabled
        changed.append("enabled")
    # 审计只记改了哪些字段名，密钥值本身不进审计表。
    record_audit(session, request, user, "update", "recharge_channel", row.id, {"fields": changed})
    await session.commit()
    await session.refresh(row)
    views = await channel_credentials.load_all(session, master)
    return success(channel_view(row, views.get(row.id, [])))


def _keys(views: list[dict[str, object]]) -> set[str]:
    return {str(item["key"]) for item in views}


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: int, request: Request, session: Session, user: SuperAdmin):
    row = await session.get(RechargeChannel, channel_id)
    if row is None:
        raise AppError(404, 1001, "支付渠道不存在")
    await session.delete(row)
    record_audit(session, request, user, "delete", "recharge_channel", channel_id)
    await session.commit()
    return success({"id": channel_id})


@router.post("/channels/{channel_id}/verify")
async def verify_channel(channel_id: int, request: Request, session: Session, user: SuperAdmin):
    """连通性自检。厂商拒绝、超时、缺配置都是"结果"而不是 HTTP 错误，前端要能逐条看到。"""
    master = master_key(request)
    # 自检要出网十几秒，不能带着行锁等远端，所以这里只读不加锁。
    row = await session.get(RechargeChannel, channel_id)
    if row is None:
        raise AppError(404, 1001, "支付渠道不存在")
    values = await channel_credentials.load_values(session, master, row.id, include_secrets=True)
    try:
        result = await registry.run_self_check(
            row.channel_type, specs.with_defaults(row.channel_type, values)
        )
    except (ProviderError, httpx.HTTPError, ValueError) as reason:
        result = SelfCheck("local_only", (Check("自检未能完成", False, str(reason)[:300]),))
    if result.stores:
        # 自检回存的东西也走同一套校验与规范化，否则人工填的那份和自动拉的那份指纹会对不上，
        # 白白多出一条"密钥被换过"的版本记录。
        values, _ = _split(row.channel_type, result.stores)
        await channel_credentials.apply(session, master, row.id, row.channel_type, values, user.id)
        record_audit(
            session,
            request,
            user,
            "update",
            "recharge_channel",
            row.id,
            {"fields": [f"credentials.{key}" for key in values], "source": "self_check"},
        )
    failed = next((item for item in result.checks if not item.ok), None)
    row.verified_at = datetime.now(UTC)
    row.verify_passed = result.passed
    row.verify_detail = None if failed is None else f"{failed.name}：{failed.detail}"[:300]
    await session.commit()
    return success(
        {"channel_id": row.id, "channel_type": row.channel_type, "passed": result.passed, **result.view()}
    )


@router.get("/orders")
async def list_orders(
    session: Session,
    user: SuperAdmin,
    status: Literal["created", "pending", "paid", "expired", "failed"] | None = None,
    channel_code: str = Query("", max_length=30),
    q: str = Query("", max_length=64),
    review: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100_000),
):
    """订单台账。review=1 只看"厂商回执金额与订单不一致"的挂起单——它们状态仍是待支付，按状态筛不出来。"""
    conditions = []
    if status:
        conditions.append(PaymentOrder.status == status)
    if channel_code:
        conditions.append(RechargeChannel.code == channel_code)
    if q:
        conditions.append(PaymentOrder.out_trade_no.ilike(f"{q}%"))
    if review:
        conditions.append(reconcile.awaiting_review)
    joined = (
        select(
            PaymentOrder,
            RechargeChannel.code,
            RechargeChannel.display_name,
            Account.username,
        )
        .join(RechargeChannel, RechargeChannel.id == PaymentOrder.channel_id)
        .join(Account, Account.id == PaymentOrder.account_id)
        .where(*conditions)
    )
    rows = (await session.execute(joined.order_by(PaymentOrder.id.desc()).limit(limit).offset(offset))).all()
    total = await session.scalar(
        select(func.count())
        .select_from(PaymentOrder)
        .join(RechargeChannel, RechargeChannel.id == PaymentOrder.channel_id)
        .join(Account, Account.id == PaymentOrder.account_id)
        .where(*conditions)
    )
    return success(
        {
            "items": [
                {
                    **order_view(order, name),
                    "channel_code": code,
                    "account_username": username,
                    "account_id": order.account_id,
                }
                for order, code, name, username in rows
            ],
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        }
    )


@router.get("/orders/{reference}")
async def order_detail(reference: str, session: Session, user: SuperAdmin):
    """单张订单的时间线：钱是谁确认收到的，只看这一屏就该能回答。"""
    order = await session.scalar(select(PaymentOrder).where(PaymentOrder.out_trade_no == reference))
    if order is None:
        raise AppError(404, 1001, "订单不存在")
    channel = await session.get(RechargeChannel, order.channel_id)
    account = await session.get(Account, order.account_id)
    events = await session.scalars(
        select(PaymentOrderEvent)
        .where(PaymentOrderEvent.order_id == order.id)
        .order_by(PaymentOrderEvent.id)
    )
    return success(
        {
            **order_view(order, channel.display_name if channel else ""),
            "channel_code": channel.code if channel else "",
            "account_username": account.username if account else "",
            "events": [
                {"kind": item.kind, "detail": item.detail, "created_at": item.created_at} for item in events
            ],
        }
    )
