from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, ApiKey, ApiLog


def day_start(day: date) -> datetime:
    """调用记录按 UTC 整日过滤，与趋势图 func.date() 的分桶口径一致。"""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def day_end(day: date) -> datetime:
    """date_to 是包含端的整日，所以右边界取次日起始，比较仍用半开区间。"""
    return day_start(day + timedelta(days=1))


def log_view(row: ApiLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "api_key_id": row.api_key_id,
        "endpoint": row.endpoint,
        "method": row.method,
        "query": row.query,
        "search_type": row.search_type,
        "top_k": row.top_k,
        "result_count": row.result_count,
        "latency_ms": row.latency_ms,
        "status_code": row.status_code,
        "error_message": row.error_message,
        "created_at": row.created_at,
    }


def key_scope(key_ids: set[int]) -> ColumnElement[bool]:
    # 空集合表示没有任何归属，直接短路成 false，而不是拼出一个匹配全表的条件。
    return ApiLog.api_key_id.in_(key_ids) if key_ids else false()


def log_conditions(
    key_ids: set[int] | None = None,
    key_id: int | None = None,
    status_code: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[ColumnElement[bool]]:
    """调用记录的过滤口径；key_ids 为 None 表示不限定归属，由调用方决定是否收窄。"""
    conditions: list[ColumnElement[bool]] = []
    if key_ids is not None:
        conditions.append(key_scope(key_ids))
    if key_id is not None:
        conditions.append(ApiLog.api_key_id == key_id)
    if status_code is not None:
        conditions.append(ApiLog.status_code == status_code)
    if date_from is not None:
        conditions.append(ApiLog.created_at >= date_from)
    if date_to is not None:
        conditions.append(ApiLog.created_at < date_to)
    return conditions


async def page_logs(
    session: AsyncSession, conditions: list[ColumnElement[bool]], limit: int, offset: int
) -> dict[str, Any]:
    where = and_(*conditions)
    rows = await session.scalars(
        select(ApiLog).where(where).order_by(ApiLog.id.desc()).limit(limit).offset(offset)
    )
    total = await session.scalar(select(func.count()).select_from(ApiLog).where(where)) or 0
    return {"items": [log_view(row) for row in rows], "total": total}


async def key_owners(session: AsyncSession, key_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not key_ids:
        return {}
    rows = await session.execute(
        select(ApiKey.id, ApiKey.name, Account.username, Account.role)
        .join(Account, Account.id == ApiKey.owner_id, isouter=True)
        .where(ApiKey.id.in_(key_ids))
    )
    return {
        key_id: {"key_name": name, "owner_username": username, "owner_role": role}
        for key_id, name, username, role in rows
    }


async def overview(
    session: AsyncSession, key_ids: set[int], balance_cent: int, active_keys: int
) -> dict[str, Any]:
    """门户概览：指标全部从 api_logs 与账本实时聚合，不另存计数字段。"""
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today - timedelta(days=6)
    scoped = key_scope(key_ids)
    latency_scope = and_(scoped, ApiLog.latency_ms.is_not(None))
    total_calls = await session.scalar(select(func.count()).select_from(ApiLog).where(scoped)) or 0
    today_calls = (
        await session.scalar(
            select(func.count()).select_from(ApiLog).where(scoped, ApiLog.created_at >= today)
        )
        or 0
    )
    average = await session.scalar(select(func.avg(ApiLog.latency_ms)).where(latency_scope))
    p95 = await session.scalar(
        select(func.percentile_cont(0.95).within_group(ApiLog.latency_ms)).where(latency_scope)
    )
    trend_rows = {
        str(day): count
        for day, count in await session.execute(
            select(func.date(ApiLog.created_at), func.count())
            .where(scoped, ApiLog.created_at >= week_start)
            .group_by(func.date(ApiLog.created_at))
        )
    }
    return {
        "counts": {
            "balance_cent": balance_cent,
            "active_keys": active_keys,
            "total_calls": total_calls,
            "today_calls": today_calls,
            "avg_latency_ms": round(float(average), 1) if average else 0,
            "p95_latency_ms": round(float(p95), 1) if p95 else 0,
        },
        "trend": [
            {
                "date": str((today - timedelta(days=offset)).date()),
                "count": trend_rows.get(str((today - timedelta(days=offset)).date()), 0),
            }
            for offset in range(6, -1, -1)
        ],
        "by_search_type": [
            {"search_type": search_type or "unknown", "count": count}
            for search_type, count in await session.execute(
                select(ApiLog.search_type, func.count())
                .where(scoped)
                .group_by(ApiLog.search_type)
                .order_by(func.count().desc())
            )
        ],
        "by_status_code": [
            {"status_code": status_code, "count": count}
            for status_code, count in await session.execute(
                select(ApiLog.status_code, func.count())
                .where(scoped)
                .group_by(ApiLog.status_code)
                .order_by(ApiLog.status_code)
            )
        ],
        "top_queries": [
            {"query": query, "count": count}
            for query, count in await session.execute(
                select(ApiLog.query, func.count().label("count"))
                .where(
                    scoped,
                    ApiLog.created_at >= week_start,
                    ApiLog.query.is_not(None),
                    ~ApiLog.query.contains("[REDACTED]"),
                )
                .group_by(ApiLog.query)
                .order_by(func.count().desc())
                .limit(10)
            )
        ],
    }
