from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import Session, SuperAdmin
from app.core.errors import AppError, success
from app.models import RechargeChannel, RechargePackage
from app.schemas.billing import ChannelInput, ChannelPatch, PackageInput
from app.services.audit import record_audit
from app.services.billing import channel_view, package_view

router = APIRouter(prefix="/v1/admin/recharge", tags=["充值系统管理"])


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


@router.get("/channels")
async def list_channels(session: Session, user: SuperAdmin):
    rows = await session.scalars(select(RechargeChannel).order_by(RechargeChannel.id))
    return success({"items": [channel_view(row) for row in rows]})


@router.post("/channels")
async def create_channel(body: ChannelInput, request: Request, session: Session, user: SuperAdmin):
    row = RechargeChannel(
        code=body.code,
        display_name=body.display_name,
        merchant_id=body.merchant_id,
        secret=body.secret.get_secret_value() if body.secret else None,
        enabled=body.enabled,
    )
    session.add(row)
    await session.flush()
    record_audit(session, request, user, "create", "recharge_channel", row.id, {"code": row.code})
    await session.commit()
    return success(channel_view(row))


@router.patch("/channels/{channel_id}")
async def patch_channel(
    channel_id: int, body: ChannelPatch, request: Request, session: Session, user: SuperAdmin
):
    row = await session.get(RechargeChannel, channel_id, with_for_update=True)
    if row is None:
        raise AppError(404, 1001, "支付渠道不存在")
    row.display_name = body.display_name
    row.merchant_id = body.merchant_id
    row.enabled = body.enabled
    if body.secret is not None:
        row.secret = body.secret.get_secret_value() or None
    # 审计只记改了哪些字段名，密钥值本身不进审计表。
    fields = ["display_name", "merchant_id", "enabled"] + (["secret"] if body.secret is not None else [])
    record_audit(session, request, user, "update", "recharge_channel", row.id, {"fields": fields})
    await session.commit()
    await session.refresh(row)
    return success(channel_view(row))


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: int, request: Request, session: Session, user: SuperAdmin):
    row = await session.get(RechargeChannel, channel_id)
    if row is None:
        raise AppError(404, 1001, "支付渠道不存在")
    await session.delete(row)
    record_audit(session, request, user, "delete", "recharge_channel", channel_id)
    await session.commit()
    return success({"id": channel_id})
