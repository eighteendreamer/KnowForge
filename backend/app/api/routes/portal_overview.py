from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PortalAccount, Session
from app.core.errors import success
from app.models import ApiKey
from app.services import billing
from app.services.api_usage import day_end, day_start, log_conditions, overview, page_logs

router = APIRouter(prefix="/v1/portal", tags=["门户数据概览"])


async def owned_key_ids(session: AsyncSession, account_id: int) -> set[int]:
    return set(await session.scalars(select(ApiKey.id).where(ApiKey.owner_id == account_id)))


@router.get("/overview")
async def portal_overview(session: Session, user: PortalAccount):
    key_ids = await owned_key_ids(session, user.id)
    active_keys = await session.scalar(
        select(func.count()).select_from(ApiKey).where(ApiKey.owner_id == user.id, ApiKey.status == "active")
    )
    balance = await billing.balance_cent(session, user.id)
    return success(await overview(session, key_ids, balance, active_keys or 0))


@router.get("/usage")
async def portal_usage(
    session: Session,
    user: PortalAccount,
    key_id: int | None = Query(None, ge=1),
    status_code: int | None = Query(None, ge=100, le=599),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    # 按 UTC 整日过滤，与概览趋势图 func.date() 的分桶口径一致，两处数字能对上。
    key_ids = await owned_key_ids(session, user.id)
    conditions = log_conditions(
        key_ids=key_ids,
        key_id=key_id,
        status_code=status_code,
        date_from=day_start(date_from) if date_from else None,
        date_to=day_end(date_to) if date_to else None,
    )
    return success(await page_logs(session, conditions, limit, offset))
