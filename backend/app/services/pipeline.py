import asyncio
import logging
import time
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID, uuid4

from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy import and_, case, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.cache import bump_revision
from app.core.config import LLM_ONLY_CONFIDENCE, Settings
from app.core.database import make_engine
from app.core.errors import AppError
from app.core.metrics import TASK_DURATION, TASK_RUNS
from app.core.model_client import ModelGateway
from app.models import Account, Category, Chunk, ChunkTag, Document, DocumentTag, ProcessingTask, Tag
from app.schemas.documents import ChunkData, DocumentAST
from app.services.indexing import chunk_data, sync_document_payload
from app.services.parser.chunker import Chunker
from app.services.parser.html_parser import ParseError, parse_html
from app.services.parser.pdf_parser import parse_pdf
from app.services.parser.vl_recognizer import recognize_missing_pages
from app.services.runtime_config import restore_snapshot, task_snapshot
from app.services.storage.file_storage import LocalStorage
from app.services.storage.qdrant_store import QdrantStore
from app.services.tagging.tagger import TaggingResult, rule_tags, tag_chunk, taxonomy_names

logger = logging.getLogger("knowforge.pipeline")

RULE_AGREED_CONFIDENCE = 1.0


def new_task(document: Document, settings: Settings, task_type: str = "ingest") -> ProcessingTask:
    return ProcessingTask(
        id=uuid4(),
        doc_id=document.id,
        task_type=task_type,
        config_snapshot=task_snapshot(settings),
    )


async def stage(
    session: AsyncSession, task: ProcessingTask, document: Document, name: str, progress: int
) -> None:
    changed = await session.scalar(
        update(Document)
        .where(Document.id == document.id, Document.status != "deleting")
        .values(status=name)
        .returning(Document.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        raise ParseError("文档已进入删除流程，停止处理")
    task.stage, task.progress = name, progress
    await session.commit()
    await session.refresh(document)


async def attach_chunk_tags(
    session: AsyncSession,
    chunk_row_id: int,
    tags: list[str],
    text: str,
    manual_tag_ids: list[int],
    auto_approve_confidence: float,
) -> None:
    """Write one chunk's generated tag links. The caller clears stale links first; manual links are re-applied.

    Shared with the re-tag path so a chunk-scoped refresh cannot reuse save_chunks, which also prunes
    chunks beyond the list it is given.
    """
    matched = set(rule_tags(text))
    for name in tags:
        confidence = RULE_AGREED_CONFIDENCE if name in matched else LLM_ONLY_CONFIDENCE
        review_status = "approved" if confidence >= auto_approve_confidence else "pending"
        statement = insert(Tag).values(
            name=name,
            normalized_name=name.casefold().strip(),
            auto_generated=True,
            confidence=confidence,
            review_status=review_status,
        )
        best = func.greatest(Tag.confidence, statement.excluded.confidence)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[Tag.normalized_name],
                set_={
                    "confidence": best,
                    # Only a still-pending auto tag is promoted, so no human decision is overridden.
                    "review_status": case(
                        (
                            and_(Tag.review_status == "pending", best >= auto_approve_confidence),
                            "approved",
                        ),
                        else_=Tag.review_status,
                    ),
                },
                where=Tag.auto_generated.is_(True),
            )
        )
        tag_id = await session.scalar(select(Tag.id).where(Tag.normalized_name == name.casefold().strip()))
        association = insert(ChunkTag).values(
            chunk_id=chunk_row_id,
            tag_id=tag_id,
            source="rule" if name in matched else "auto",
            confidence=confidence,
        )
        await session.execute(association.on_conflict_do_nothing())
    for tag_id in manual_tag_ids:
        await session.execute(
            insert(ChunkTag)
            .values(chunk_id=chunk_row_id, tag_id=tag_id, source="manual", confidence=1)
            .on_conflict_do_nothing()
        )


async def save_chunks(
    session: AsyncSession,
    document: Document,
    chunks: list[ChunkData],
    results: list[TaggingResult],
    categories: dict[str, int],
    auto_approve_confidence: float,
) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(734602)"))
    manual_tags = list(
        await session.scalars(
            select(DocumentTag.tag_id).where(
                DocumentTag.doc_id == document.id, DocumentTag.source == "manual"
            )
        )
    )
    for chunk, result in zip(chunks, results, strict=True):
        values = chunk.model_dump(exclude={"metadata", "doc_id"})
        values.update(
            doc_id=document.id,
            metadata=chunk.metadata,
            category_id=document.manual_category_id or categories.get(result.category),
            difficulty=result.difficulty,
            qdrant_point_id=UUID(hex=chunk.chunk_id.removeprefix("chunk_")),
        )
        statement = insert(Chunk.metadata.tables[Chunk.__tablename__]).values(**values)
        updates = {key: value for key, value in values.items() if key != "chunk_id"}
        updates["updated_at"] = func.now()
        upsert = statement.on_conflict_do_update(index_elements=[Chunk.chunk_id], set_=updates).returning(
            Chunk.id
        )
        chunk_id = (await session.execute(upsert)).scalar_one()
        await session.execute(
            delete(ChunkTag).where(ChunkTag.chunk_id == chunk_id, ChunkTag.source != "manual")
        )
        await attach_chunk_tags(
            session, chunk_id, result.tags, chunk.text, manual_tags, auto_approve_confidence
        )
    await session.execute(delete(Chunk).where(Chunk.doc_id == document.id, Chunk.chunk_index >= len(chunks)))
    await session.execute(
        delete(DocumentTag).where(DocumentTag.doc_id == document.id, DocumentTag.source != "manual")
    )
    tag_ids = await session.scalars(
        select(ChunkTag.tag_id)
        .join(Chunk, ChunkTag.chunk_id == Chunk.id)
        .where(Chunk.doc_id == document.id)
        .distinct()
    )
    for tag_id in tag_ids:
        await session.execute(
            insert(DocumentTag)
            .values(doc_id=document.id, tag_id=tag_id, source="auto", confidence=0.7)
            .on_conflict_do_nothing()
        )
    votes = [result.category for result in results if result.category]
    if votes:
        document.auto_category_id = categories[Counter(votes).most_common(1)[0][0]]
    document.total_chunks = len(chunks)
    await session.commit()


async def known_tag_candidates(
    session: AsyncSession, category_paths: list[str], limit: int = 100
) -> list[str]:
    """Approved tag names offered to the tagger, ordered for reproducibility and never holding a category name."""
    blocked = taxonomy_names(category_paths)
    return [
        name
        for name in await session.scalars(
            select(Tag.name).where(Tag.review_status == "approved").order_by(Tag.name).limit(limit)
        )
        if name.casefold() not in blocked
    ]


async def ingest(
    session: AsyncSession,
    task: ProcessingTask,
    document: Document,
    settings: Settings,
    gateway: ModelGateway,
    store: QdrantStore,
    storage: LocalStorage,
) -> None:
    path = storage.resolve(document.source_path)
    await stage(session, task, document, "parsing", 5)
    parsed = parse_pdf(path, settings) if document.file_type == "pdf" else parse_html(path)
    if parsed.recognition_pages:
        parsed = await recognize_missing_pages(path, parsed, gateway, settings)
    uploader = await session.get(Account, document.uploader_id) if document.uploader_id else None
    ast = DocumentAST(
        doc_id=document.doc_id,
        title=document.title,
        source_type=document.file_type,
        source_path=document.source_path,
        upload_time=document.upload_time,
        uploader=uploader.username if uploader else "system",
        elements=parsed.elements,
        stats={
            "total_elements": len(parsed.elements),
            "total_pages": parsed.total_pages,
            "total_chars": sum(element.char_count for element in parsed.elements),
        },
    )
    document.ast_path = f"ast/{document.doc_id}.json"
    storage.write_json(document.ast_path, ast.model_dump_json())
    document.total_pages = parsed.total_pages
    await stage(session, task, document, "chunking", 20)
    chunker = Chunker(
        settings.tokenizer_path, settings.chunk_size, settings.chunk_overlap, settings.embedding_max_tokens
    )
    chunks = chunker.build(document.doc_id, parsed.elements, document.original_filename)
    await stage(session, task, document, "embedding", 30)
    vectors = await gateway.embed([chunk.text_with_context for chunk in chunks])
    await stage(session, task, document, "tagging", 55)
    categories = {row.path: row.id for row in await session.scalars(select(Category))}
    known_tags = await known_tag_candidates(session, list(categories))
    results: list[TaggingResult] = []
    for index, chunk in enumerate(chunks):
        results.append(await tag_chunk(chunk.text_with_context, list(categories), known_tags, gateway))
        task.progress = 55 + int(30 * (index + 1) / len(chunks))
        await session.commit()
    await save_chunks(session, document, chunks, results, categories, settings.tag_auto_approve_confidence)
    await stage(session, task, document, "indexing", 90)
    await store.ensure_collection()
    await store.delete_document(document.doc_id)
    await store.upsert(await chunk_data(session, document), vectors, document.is_public)
    changed = await session.scalar(
        update(Document)
        .where(Document.id == document.id, Document.status != "deleting")
        .values(status="ready", parse_error=None)
        .returning(Document.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        raise ParseError("文档已进入删除流程，停止发布索引")
    await session.refresh(document)
    task.status, task.stage, task.progress = "succeeded", "ready", 100
    task.finished_at = datetime.now(UTC)
    await bump_revision(session)
    await session.commit()


async def process_task(
    task_id: UUID, settings: Settings, gateway_override: ModelGateway | None = None
) -> None:
    engine = make_engine(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    client = AsyncQdrantClient(
        url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value() or None
    )
    gateway: ModelGateway | None = None
    try:
        async with engine.connect() as connection:
            sessions = async_sessionmaker(connection, expire_on_commit=False)
            async with sessions() as session:
                task = await session.get(ProcessingTask, task_id)
                if task is None or task.status == "succeeded":
                    return
                lock_id = task.doc_id
                locked = await session.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id})
                if not locked:
                    return
                started = time.perf_counter()
                task_type = task.task_type
                outcome = "skipped"
                try:
                    await session.refresh(task)
                    document = await session.get(Document, task.doc_id)
                    if document is None or task.status == "succeeded":
                        return
                    config = restore_snapshot(settings, task.config_snapshot)
                    gateway = gateway_override or ModelGateway(config, redis)
                    store, storage = QdrantStore(client, config), LocalStorage(config)
                    task.status, task.started_at, task.error_message = "running", datetime.now(UTC), None
                    task.attempts += 1
                    await session.commit()
                    if task.task_type == "delete":
                        await store.delete_document(document.doc_id)
                        storage.delete(document.source_path)
                        if document.ast_path:
                            storage.delete(document.ast_path)
                        await session.delete(document)
                        await bump_revision(session)
                        await session.commit()
                    elif task.task_type == "sync_payload":
                        await sync_document_payload(session, document, store)
                        task.status, task.progress, task.finished_at = "succeeded", 100, datetime.now(UTC)
                        await bump_revision(session)
                        await session.commit()
                    else:
                        await ingest(session, task, document, config, gateway, store, storage)
                    outcome = "succeeded"
                except Exception as exc:
                    outcome = "failed"
                    await session.rollback()
                    task = await session.get(ProcessingTask, task_id)
                    if task:
                        document = await session.get(Document, task.doc_id)
                        message = (
                            exc.message
                            if isinstance(exc, AppError)
                            else str(exc)
                            if isinstance(exc, ParseError)
                            else f"处理失败：{type(exc).__name__} {' '.join(str(exc).split())[:160]}".strip()
                        )
                        task.status, task.error_message, task.finished_at = (
                            "failed",
                            message,
                            datetime.now(UTC),
                        )
                        if document and task.task_type in {"ingest", "reindex"}:
                            await session.execute(
                                update(Document)
                                .where(Document.id == document.id, Document.status != "deleting")
                                .values(status="failed", parse_error=message)
                            )
                        await session.commit()
                    logger.error("task_failed task_id=%s error_type=%s", task_id, type(exc).__name__)
                finally:
                    if outcome != "skipped":
                        TASK_RUNS.labels(task_type, outcome).inc()
                        TASK_DURATION.labels(task_type, outcome).observe(time.perf_counter() - started)
                    await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
                    await session.commit()
    finally:
        if gateway is not None and gateway_override is None:
            await gateway.close()
        await client.close()
        await redis.aclose()
        await engine.dispose()


def run_task(task_id: str) -> None:
    asyncio.run(process_task(UUID(task_id), Settings()))
