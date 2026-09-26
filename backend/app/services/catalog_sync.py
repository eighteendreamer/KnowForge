from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import bump_revision
from app.core.config import Settings
from app.models import Document, ProcessingTask
from app.services.pipeline import new_task


async def lock_catalog(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(734602)"))


async def enqueue_catalog_sync(
    session: AsyncSession, document_ids: set[int], settings: Settings
) -> list[ProcessingTask]:
    tasks = []
    if document_ids:
        documents = await session.scalars(
            select(Document).where(Document.id.in_(document_ids), Document.status == "ready")
        )
        for document in documents:
            task = new_task(document, settings, "sync_payload")
            session.add(task)
            tasks.append(task)
    await bump_revision(session)
    return tasks
