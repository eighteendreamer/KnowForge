from datetime import UTC, date, datetime

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.deps import Session, SuperAdmin
from app.core.errors import AppError, success
from app.core.security import generate_api_key
from app.models import Account, ApiKey
from app.schemas.identity import ApiKeyInput, ApiKeyPatch
from app.services.api_usage import day_end, day_start, key_owners, log_conditions, page_logs
from app.services.audit import record_audit

router = APIRouter(prefix="/v1/admin/api-keys", tags=["API Key 管理"])


def key_view(row: ApiKey) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "key_prefix": row.key_prefix,
        "status": row.status,
        "scopes": row.scopes,
        "rate_limit_per_day": row.rate_limit_per_day,
        "rate_limit_per_minute": row.rate_limit_per_minute,
        "total_calls": row.total_calls,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "last_used_at": row.last_used_at,
    }


@router.get("")
async def list_keys(
    session: Session,
    user: SuperAdmin,
    q: str = Query("", max_length=100),
    owner_id: int | None = Query(None, ge=1),
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    filters = []
    if q:
        filters.append(ApiKey.name.icontains(q, autoescape=True))
    if owner_id:
        filters.append(ApiKey.owner_id == owner_id)
    if status:
        filters.append(ApiKey.status == status)
    rows = (
        await session.execute(
            select(ApiKey, Account.username, Account.role)
            .join(Account, Account.id == ApiKey.owner_id, isouter=True)
            .where(*filters)
            .order_by(ApiKey.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    total = await session.scalar(select(func.count()).select_from(ApiKey).where(*filters))
    return success(
        {
            "items": [
                {**key_view(row), "owner_username": username, "owner_role": role}
                for row, username, role in rows
            ],
            "total": total,
        }
    )


@router.get("/accounts")
async def list_key_owners(session: Session, user: SuperAdmin):
    """密钥归属下拉的数据源，只返回真正持有过密钥的账号。"""
    rows = await session.execute(
        select(Account.id, Account.username, Account.role, func.count(ApiKey.id))
        .join(ApiKey, ApiKey.owner_id == Account.id, isouter=True)
        .group_by(Account.id)
        .order_by(Account.id)
    )
    return success(
        {"items": [{"id": row[0], "username": row[1], "role": row[2], "keys": row[3]} for row in rows]}
    )


@router.post("")
async def create_key(body: ApiKeyInput, request: Request, session: Session, user: SuperAdmin):
    if body.expires_at and body.expires_at <= datetime.now(UTC):
        raise AppError(400, 1001, "有效期必须晚于当前时间")
    raw, digest = generate_api_key()
    row = ApiKey(**body.model_dump(), key_hash=digest, key_prefix=raw[:12], owner_id=user.id)
    session.add(row)
    await session.flush()
    record_audit(session, request, user, "create", "api_key", row.id)
    await session.commit()
    return success({**key_view(row), "key": raw})


@router.patch("/{key_id}")
async def patch_key(key_id: int, body: ApiKeyPatch, request: Request, session: Session, user: SuperAdmin):
    row = await session.get(ApiKey, key_id, with_for_update=True)
    if row is None:
        raise AppError(404, 1001, "API Key 不存在")
    if row.status == "revoked":
        raise AppError(400, 1001, "已吊销的 API Key 不能恢复")
    if body.expires_at and body.expires_at <= datetime.now(UTC):
        raise AppError(400, 1001, "有效期必须晚于当前时间")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None and field != "expires_at":
            raise AppError(400, 1001, "名称、限额和状态不能设为空")
        setattr(row, field, value)
    record_audit(session, request, user, "update", "api_key", row.id)
    await session.commit()
    return success(key_view(row))


@router.delete("/{key_id}")
async def revoke_key(key_id: int, request: Request, session: Session, user: SuperAdmin):
    row = await session.get(ApiKey, key_id, with_for_update=True)
    if row is None:
        raise AppError(404, 1001, "API Key 不存在")
    row.status = "revoked"
    record_audit(session, request, user, "revoke", "api_key", row.id)
    await session.commit()
    return success({"id": key_id, "status": "revoked"})


@router.get("/logs")
@router.get("/{key_id}/logs")
async def key_logs(
    session: Session,
    user: SuperAdmin,
    key_id: int | None = None,
    status_code: int | None = Query(None, ge=100, le=599),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """不带 key_id 时是全部用户的请求流水，带 key_id 时收窄到单把密钥，两条路径共用同一查询。"""
    conditions = log_conditions(
        key_id=key_id,
        status_code=status_code,
        date_from=day_start(date_from) if date_from else None,
        date_to=day_end(date_to) if date_to else None,
    )
    page = await page_logs(session, conditions, limit, offset)
    owners = await key_owners(session, {row["api_key_id"] for row in page["items"] if row["api_key_id"]})
    for row in page["items"]:
        row.update(owners.get(row["api_key_id"], {}))
    return success(page)
