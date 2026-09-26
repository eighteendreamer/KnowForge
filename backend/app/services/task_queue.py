import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from celery import Celery
from kombu.exceptions import OperationalError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.operations import IndexRebuild, ProcessingTask

logger = logging.getLogger("knowforge.queue")

CONSUMER_TTL_SECONDS = 10.0
CONSUMER_NEGATIVE_TTL_SECONDS = 2.0
PING_TIMEOUT_SECONDS = 1.0
BUSY_WORKER_FRESHNESS_SECONDS = 120.0

_consumers: dict[str, tuple[float, bool]] = {}


def _ping(celery: Celery) -> bool:
    """Solo-pool workers cannot answer control replies while running a task, so silence is not proof of death."""
    return bool(celery.control.inspect(timeout=PING_TIMEOUT_SECONDS).ping())


async def _worker_is_busy(session: AsyncSession) -> bool:
    """A row a worker is actively updating is persisted proof that the queue is being drained."""
    fresh_after = datetime.now(UTC) - timedelta(seconds=BUSY_WORKER_FRESHNESS_SECONDS)
    for model in (ProcessingTask, IndexRebuild):
        running = await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.status == "running", model.updated_at > fresh_after)
        )
        if running:
            return True
    return False


async def consumer_online(celery: Celery, session: AsyncSession) -> bool:
    key = str(celery.conf.broker_url)
    expires, cached = _consumers.get(key, (0.0, False))
    if expires > time.monotonic():
        return cached
    try:
        online = await asyncio.wait_for(asyncio.to_thread(_ping, celery), PING_TIMEOUT_SECONDS + 0.5)
    except Exception:
        online = False
    if not online:
        online = await _worker_is_busy(session)
    ttl = CONSUMER_TTL_SECONDS if online else CONSUMER_NEGATIVE_TTL_SECONDS
    _consumers[key] = (time.monotonic() + ttl, online)
    return online


async def require_consumer(celery: Celery, session: AsyncSession) -> None:
    if not await consumer_online(celery, session):
        raise AppError(409, 1001, "暂无可用 Worker：任务队列没有被消费，请先启动 celery worker 再提交")


async def dispatch_named(celery: Celery, session: AsyncSession, name: str, args: list) -> None:
    await require_consumer(celery, session)
    try:
        await asyncio.to_thread(celery.send_task, name, args=args, retry=False)
    except OperationalError as exc:
        logger.warning("task_dispatch_failed task_name=%s args=%s", name, args)
        raise AppError(503, 5002, "任务已记录但入队失败，Redis broker 不可用，请稍后重试") from exc


async def dispatch(celery: Celery, session: AsyncSession, task_id: str) -> None:
    await dispatch_named(celery, session, "knowforge.process", [task_id])
