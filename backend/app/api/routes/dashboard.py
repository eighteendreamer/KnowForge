from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import Admin, Session, SuperAdmin
from app.core.errors import success
from app.models import ApiLog, AuditLog, Chunk, Document, ProcessingTask, Tag

router = APIRouter(prefix="/v1/admin", tags=["仪表盘与审计"])


@router.get("/dashboard")
async def dashboard(session: Session, user: Admin):
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    counts = {}
    for name, model in [("documents", Document), ("chunks", Chunk), ("tags", Tag)]:
        counts[name] = await session.scalar(select(func.count()).select_from(model))
    counts["today_calls"] = await session.scalar(
        select(func.count()).select_from(ApiLog).where(ApiLog.created_at >= today)
    )
    statuses = {
        status: count
        for status, count in await session.execute(
            select(Document.status, func.count()).group_by(Document.status)
        )
    }
    trend_rows = await session.execute(
        select(func.date(ApiLog.created_at), func.count())
        .where(ApiLog.created_at >= today - timedelta(days=6))
        .group_by(func.date(ApiLog.created_at))
    )
    trend = {str(date): count for date, count in trend_rows}
    popular = await session.execute(
        select(ApiLog.query, func.count().label("count"))
        .where(
            ApiLog.created_at >= today - timedelta(days=6),
            ApiLog.query.is_not(None),
            ~ApiLog.query.contains("[REDACTED]"),
        )
        .group_by(ApiLog.query)
        .order_by(func.count().desc())
        .limit(10)
    )
    recent = await session.execute(
        select(ProcessingTask, Document.title)
        .join(Document, Document.id == ProcessingTask.doc_id)
        .order_by(ProcessingTask.updated_at.desc())
        .limit(8)
    )
    return success(
        {
            "counts": counts,
            "document_statuses": statuses,
            "trend": [
                {
                    "date": str((today - timedelta(days=offset)).date()),
                    "count": trend.get(str((today - timedelta(days=offset)).date()), 0),
                }
                for offset in range(6, -1, -1)
            ],
            "popular_queries": [{"query": query, "count": count} for query, count in popular],
            "recent_tasks": [
                {
                    "id": str(task.id),
                    "title": title,
                    "status": task.status,
                    "stage": task.stage,
                    "progress": task.progress,
                    "updated_at": task.updated_at,
                }
                for task, title in recent
            ],
        }
    )


@router.get("/audit-logs")
async def audit_logs(
    session: Session, user: SuperAdmin, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    rows = await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).offset(offset).limit(limit))
    return success(
        {
            "items": [
                {
                    "id": row.id,
                    "operator_id": row.operator_id,
                    "action": row.action,
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "client_ip": row.client_ip,
                    "details": row.details,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        }
    )
