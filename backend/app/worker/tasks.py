import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.models import ProcessingTask
from app.services.payments.reconcile import reconcile
from app.services.pipeline import run_task
from app.services.rebuild import run_rebuild_task
from app.worker.celery_app import celery_app


@celery_app.task(name="knowforge.process")
def process_document(task_id: str) -> None:
    run_task(task_id)


@celery_app.task(name="knowforge.rebuild")
def rebuild_index(rebuild_id: str) -> None:
    run_rebuild_task(rebuild_id)


async def pending_tasks() -> list[str]:
    engine = make_engine(Settings())
    try:
        async with make_session_factory(engine)() as session:
            rows = await session.scalars(
                select(ProcessingTask.id)
                .where(
                    or_(
                        ProcessingTask.status == "pending",
                        (ProcessingTask.status == "running")
                        & (ProcessingTask.updated_at < datetime.now(UTC) - timedelta(minutes=5)),
                    )
                )
                .order_by(ProcessingTask.created_at)
                .limit(100)
            )
            return [str(row) for row in rows]
    finally:
        await engine.dispose()


@celery_app.task(name="knowforge.recover")
def recover_pending() -> None:
    for task_id in asyncio.run(pending_tasks()):
        celery_app.send_task("knowforge.process", args=[task_id])


@celery_app.task(name="knowforge.reconcile_orders")
def reconcile_payment_orders() -> None:
    asyncio.run(reconcile(Settings()))
