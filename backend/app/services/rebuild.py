import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.cache import revision
from app.core.config import Settings
from app.core.database import make_engine
from app.core.model_client import ModelGateway
from app.models import Document, IndexRebuild
from app.services.indexing import chunk_data
from app.services.runtime_config import build_settings
from app.services.storage.qdrant_store import QdrantStore

logger = logging.getLogger("knowforge.rebuild")


async def _mark_failed(defaults: Settings, rebuild_id: UUID, message: str) -> None:
    engine = make_engine(defaults)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            row = await session.get(IndexRebuild, rebuild_id)
            if row is not None and row.status in {"pending", "running"}:
                row.status, row.error_message = "failed", message[:2000]
                await session.commit()
    finally:
        await engine.dispose()


async def run_rebuild(rebuild_id: UUID, defaults: Settings) -> None:
    engine = make_engine(defaults)
    redis = Redis.from_url(defaults.redis_url, decode_responses=True)
    client = AsyncQdrantClient(
        url=defaults.qdrant_url, api_key=defaults.qdrant_api_key.get_secret_value() or None
    )
    gateway: ModelGateway | None = None
    try:
        async with engine.connect() as connection:
            sessions = async_sessionmaker(connection, expire_on_commit=False)
            async with sessions() as session:
                row = await session.scalar(
                    select(IndexRebuild).where(IndexRebuild.id == rebuild_id).with_for_update()
                )
                if row is None or row.status not in {"pending", "running"}:
                    return
                target = build_settings(
                    defaults, row.target_values, row.target_fingerprint, row.target_configuration_id
                )
                if row.started_at is None:
                    row.started_at = datetime.now(UTC)
                row.status = "running"
                await session.commit()
                gateway = ModelGateway(target, redis)
                store = QdrantStore(client, target)
                await store.ensure_collection()
                while True:
                    document = await session.scalar(
                        select(Document)
                        .where(
                            Document.status == "ready",
                            Document.id > (row.checkpoint_document_id or 0),
                        )
                        .order_by(Document.id)
                        .limit(1)
                    )
                    if document is None:
                        break
                    chunks = await chunk_data(session, document)
                    vectors = await gateway.embed([chunk.text_with_context for chunk in chunks], "batch")
                    await store.upsert(chunks, vectors, document.is_public)
                    row.completed_documents += 1
                    row.checkpoint_document_id = document.id
                    await session.commit()
                expected = await session.scalar(
                    select(func.coalesce(func.sum(Document.total_chunks), 0)).where(
                        Document.status == "ready"
                    )
                )
                actual = await store.point_count()
                if actual != expected:
                    raise RuntimeError(f"重建点数不一致：{actual}/{expected}")
                row.finish_revision = await revision(session)
                row.status, row.finished_at = "evaluating", datetime.now(UTC)
                await session.commit()
    except BaseException as exc:
        detail = " ".join(str(exc).split())[:160]
        message = f"重建失败：{type(exc).__name__} {detail}".strip()[:2000]
        logger.error("rebuild_failed rebuild_id=%s error_type=%s", rebuild_id, type(exc).__name__)
        await _mark_failed(defaults, rebuild_id, message)
    finally:
        if gateway is not None:
            await gateway.close()
        await client.close()
        await redis.aclose()
        await engine.dispose()


def run_rebuild_task(rebuild_id: str) -> None:
    asyncio.run(run_rebuild(UUID(rebuild_id), Settings()))
