"""Re-tag the chunks that currently have no tag link at all, then re-push their documents' payloads.

Deleting a tag cascades away its chunk links (`chunk_tags.tag_id ON DELETE CASCADE`), so cleaning a polluted tag
can strand chunks with zero tags. Re-running the tagger on exactly those chunks restores coverage without
re-parsing, re-chunking or re-embedding anything — tags live in the Qdrant payload, not in the dense vector.
"""

import argparse
import asyncio
import json

from redis.asyncio import Redis
from sqlalchemy import distinct, func, select

from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.core.model_client import ModelGateway
from app.models import Category, Chunk, ChunkTag, Document, Tag
from app.services.catalog_sync import enqueue_catalog_sync
from app.services.pipeline import attach_chunk_tags, known_tag_candidates
from app.services.runtime_config import active_settings
from app.services.tagging.tagger import tag_chunk
from app.services.task_queue import dispatch
from app.worker.celery_app import celery_app

BATCH = 8


async def untagged_chunks(session) -> list[Chunk]:
    ready = select(Document.id).where(Document.status == "ready")
    linked = select(distinct(ChunkTag.chunk_id))
    return list(
        await session.scalars(
            select(Chunk).where(Chunk.doc_id.in_(ready), Chunk.id.notin_(linked)).order_by(Chunk.id)
        )
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true", help="真正写库并排队同步；不加就只报告将要处理多少分块"
    )
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个分块，用于先小批量验证")
    args = parser.parse_args()

    defaults = Settings()
    engine = make_engine(defaults)
    try:
        async with make_session_factory(engine)() as session:
            settings = await active_settings(session, defaults)
            rows = await untagged_chunks(session)
            if args.limit:
                rows = rows[: args.limit]
            if not rows:
                print(json.dumps({"untagged_chunks": 0}, ensure_ascii=False), flush=True)
                return
            if not args.apply:
                print(
                    json.dumps(
                        {
                            "untagged_chunks": len(rows),
                            "documents": len({row.doc_id for row in rows}),
                            "gate": settings.tag_auto_approve_confidence,
                            "would_enqueue_sync_tasks_for": len({row.doc_id for row in rows}),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return
            paths = list(await session.scalars(select(Category.path)))
            known = await known_tag_candidates(session, paths)
            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            gateway = ModelGateway(settings, redis)
            written, failures = 0, []
            try:
                for start in range(0, len(rows), BATCH):
                    batch = rows[start : start + BATCH]
                    results = await asyncio.gather(
                        *(tag_chunk(chunk.text_with_context, paths, known, gateway) for chunk in batch),
                        return_exceptions=True,
                    )
                    for chunk, result in zip(batch, results, strict=True):
                        if isinstance(result, Exception):
                            failures.append(f"{chunk.chunk_id}: {type(result).__name__}"[:120])
                            continue
                        await attach_chunk_tags(
                            session,
                            chunk.id,
                            result.tags,
                            chunk.text,
                            [],
                            settings.tag_auto_approve_confidence,
                        )
                        written += 1
                    await session.commit()
                    print(
                        json.dumps(
                            {"retagged": written, "of": len(rows), "failed": len(failures)},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                documents = {chunk.doc_id for chunk in rows}
                tasks = await enqueue_catalog_sync(session, documents, settings)
                await session.commit()
            finally:
                await gateway.close()
                await redis.aclose()
            for task in tasks:
                await dispatch(celery_app, session, str(task.id))
            approved = await session.scalar(
                select(func.count()).select_from(Tag).where(Tag.review_status == "approved")
            )
            print(
                json.dumps(
                    {
                        "retagged_chunks": written,
                        "retag_failures": failures,
                        "docs_enqueued": len(tasks),
                        "gate": settings.tag_auto_approve_confidence,
                        "approved_tags_now": approved,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
