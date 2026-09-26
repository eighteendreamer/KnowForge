import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import Admin, Session
from app.core.errors import AppError, success
from app.models import Account, Document, ProcessingTask
from app.services.audit import record_audit
from app.services.task_queue import dispatch, require_consumer

router = APIRouter(prefix="/v1/admin/tasks", tags=["处理任务"])


def task_view(task: ProcessingTask, doc_id: str, title: str) -> dict:
    return {
        "id": str(task.id),
        "doc_id": doc_id,
        "title": title,
        "task_type": task.task_type,
        "status": task.status,
        "stage": task.stage,
        "progress": task.progress,
        "attempts": task.attempts,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


async def latest_tasks(session, limit: int = 100) -> list[dict]:
    rows = await session.execute(
        select(ProcessingTask, Document.doc_id, Document.title)
        .join(Document, ProcessingTask.doc_id == Document.id)
        .order_by(ProcessingTask.created_at.desc())
        .limit(limit)
    )
    return [task_view(task, doc_id, title) for task, doc_id, title in rows]


@router.get("")
async def list_tasks(session: Session, user: Admin, limit: int = Query(100, ge=1, le=200)):
    return success({"items": await latest_tasks(session, limit)})


@router.get("/events")
async def task_events(request: Request, user: Admin):
    async def stream():
        previous = ""
        while not await request.is_disconnected():
            async with request.app.state.sessions() as session:
                account = await session.get(Account, user.id)
                if account is None or account.status != "active":
                    break
                payload = json.dumps(jsonable_encoder(await latest_tasks(session)), ensure_ascii=False)
            if payload != previous:
                yield f"event: tasks\ndata: {payload}\n\n"
                previous = payload
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{task_id}/retry")
async def retry_task(task_id: UUID, request: Request, session: Session, user: Admin):
    task = await session.get(ProcessingTask, task_id, with_for_update=True)
    if task is None:
        raise AppError(404, 1001, "任务不存在")
    if task.status != "failed":
        raise AppError(400, 1001, "仅失败任务可重试")
    document = await session.get(Document, task.doc_id)
    if document is None or (document.status == "deleting" and task.task_type != "delete"):
        raise AppError(400, 1001, "文档已删除或正在删除")
    await require_consumer(request.app.state.celery, session)
    task.status, task.error_message = "pending", None
    if task.task_type in {"ingest", "reindex"}:
        document.status = "pending"
    record_audit(session, request, user, "retry", "task", str(task.id))
    await session.commit()
    await dispatch(request.app.state.celery, session, str(task.id))
    return success({"task_id": str(task.id), "status": "pending"})
