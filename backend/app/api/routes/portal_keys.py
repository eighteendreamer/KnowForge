from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PortalAccount, Session
from app.api.routes.api_keys import key_view
from app.core.errors import AppError, success
from app.core.security import generate_api_key
from app.models import ApiKey
from app.schemas.identity import PortalKeyInput, PortalKeyPatch
from app.services.api_usage import log_conditions, page_logs
from app.services.audit import record_audit

router = APIRouter(prefix="/v1/portal/keys", tags=["门户 API 密钥"])


async def owned_key(session: AsyncSession, account_id: int, key_id: int, *, lock: bool = False) -> ApiKey:
    statement = select(ApiKey).where(ApiKey.id == key_id, ApiKey.owner_id == account_id)
    if lock:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        # 不区分“不存在”和“不属于你”，避免用接口枚举他人的密钥编号。
        raise AppError(404, 1001, "API Key 不存在")
    return row


@router.get("")
async def list_keys(session: Session, user: PortalAccount):
    rows = await session.scalars(select(ApiKey).where(ApiKey.owner_id == user.id).order_by(ApiKey.id.desc()))
    return success({"items": [key_view(row) for row in rows]})


@router.post("")
async def create_key(body: PortalKeyInput, request: Request, session: Session, user: PortalAccount):
    if body.expires_at and body.expires_at <= datetime.now(UTC):
        raise AppError(400, 1001, "有效期必须晚于当前时间")
    raw, digest = generate_api_key()
    row = ApiKey(
        key_hash=digest, key_prefix=raw[:12], name=body.name, owner_id=user.id, expires_at=body.expires_at
    )
    session.add(row)
    await session.flush()
    record_audit(session, request, user, "create", "api_key", row.id)
    await session.commit()
    return success({**key_view(row), "key": raw})


@router.patch("/{key_id}")
async def patch_key(
    key_id: int, body: PortalKeyPatch, request: Request, session: Session, user: PortalAccount
):
    row = await owned_key(session, user.id, key_id, lock=True)
    if row.status == "revoked":
        raise AppError(400, 1001, "已吊销的 API Key 不能恢复")
    if body.expires_at and body.expires_at <= datetime.now(UTC):
        raise AppError(400, 1001, "有效期必须晚于当前时间")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None and field != "expires_at":
            raise AppError(400, 1001, "名称和状态不能设为空")
        setattr(row, field, value)
    record_audit(session, request, user, "update", "api_key", row.id)
    await session.commit()
    return success(key_view(row))


@router.delete("/{key_id}")
async def revoke_key(key_id: int, request: Request, session: Session, user: PortalAccount):
    row = await owned_key(session, user.id, key_id, lock=True)
    row.status = "revoked"
    record_audit(session, request, user, "revoke", "api_key", row.id)
    await session.commit()
    return success({"id": key_id, "status": "revoked"})


@router.get("/{key_id}/logs")
async def key_logs(
    key_id: int,
    session: Session,
    user: PortalAccount,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    row = await owned_key(session, user.id, key_id)
    return success(await page_logs(session, log_conditions(key_ids={row.id}), limit, offset))
