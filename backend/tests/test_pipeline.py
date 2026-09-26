import json
from uuid import uuid4

import httpx
import pytest
from openai import AsyncOpenAI
from sqlalchemy import delete, func, select

from app.core.database import make_engine, make_session_factory
from app.core.model_client import ModelGateway
from app.models import Category, Chunk, Document, ProcessingTask, Tag
from app.services.pipeline import ingest, new_task, process_task
from app.services.storage.file_storage import LocalStorage
from app.services.storage.qdrant_store import QdrantStore


async def test_ingestion_persists_chunks_and_rebuild_is_idempotent(context):
    settings = context["runtime"]
    storage = LocalStorage(settings)
    storage.write_json(
        "documents/test.html",
        "<html><h1>Redis</h1><p>Redis cache penetration is prevented with Bloom filters and negative caching.</p></html>",
    )
    calls = []

    def handler(request):
        calls.append(request.url.path)
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": body["model"],
                    "data": [
                        {"index": index, "object": "embedding", "embedding": [1.0] + [0.0] * 4095}
                        for index in range(len(body["input"]))
                    ],
                    "usage": {"prompt_tokens": 20, "total_tokens": 20},
                },
            )
        content = json.dumps(
            {
                "tags": ["Redis", "缓存穿透", "布隆过滤器"],
                "category": "后端开发/缓存/Redis",
                "difficulty": "中级",
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
            },
        )

    client = AsyncOpenAI(
        api_key="test-only",
        base_url="https://model.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    gateway = ModelGateway(settings, context["app"].state.redis, client)
    store = QdrantStore(context["app"].state.qdrant, settings)
    try:
        async with context["sessions"]() as session:
            document = Document(
                doc_id="doc_" + uuid4().hex,
                title="Redis",
                original_filename="test.html",
                file_type="html",
                file_size=100,
                source_path="documents/test.html",
                uploader_id=context["admin"].id,
            )
            session.add(document)
            await session.flush()
            task = new_task(document, settings)
            session.add(task)
            await session.commit()
            await ingest(session, task, document, settings, gateway, store, storage)
            assert document.status == "ready"
            assert task.status == "succeeded"
            assert (
                await session.scalar(
                    select(func.count()).select_from(Chunk).where(Chunk.doc_id == document.id)
                )
                == document.total_chunks
            )
            assert storage.resolve(document.ast_path).exists()
            statuses = {tag.name: tag.review_status for tag in await session.scalars(select(Tag))}
            # Rule agreement scores 1.0 and a plain LLM tag 0.7; the default gate sits at 0.7, so both are trusted.
            assert statuses["Redis"] == "approved"
            assert statuses["缓存穿透"] == "approved"
            count = document.total_chunks
            human = next(tag for tag in await session.scalars(select(Tag)) if tag.name == "布隆过滤器")
            human.review_status = "rejected"
            await session.commit()
            await ingest(session, task, document, settings, gateway, store, storage)
            reopened = {tag.name: tag.review_status for tag in await session.scalars(select(Tag))}
            # Re-ingesting must never resurrect a human rejection.
            assert reopened["布隆过滤器"] == "rejected"
            assert (
                await session.scalar(
                    select(func.count()).select_from(Chunk).where(Chunk.doc_id == document.id)
                )
                == count
            )
            assert (await store.client.count(store.collection, exact=True)).count == count
            points = (await store.client.scroll(store.collection, with_payload=True))[0]
            approved = {
                tag.name for tag in await session.scalars(select(Tag).where(Tag.review_status == "approved"))
            }
            # Payload carries only approved names, so a rejected tag can never reach a caller's filters.
            assert set(points[0].payload["tags"]) <= approved
            assert "Redis" in points[0].payload["tags"]
            assert "布隆过滤器" not in points[0].payload["tags"]
            assert points[0].payload["source"] == "test.html"
            assert calls.count("/v1/embeddings") == 2
    finally:
        await gateway.close()


async def test_stage_cannot_revive_deleting_document(context):
    from app.services.parser.html_parser import ParseError
    from app.services.pipeline import stage

    async with context["sessions"]() as session:
        document = Document(
            doc_id="doc_" + uuid4().hex,
            title="Deleting",
            original_filename="test.html",
            file_type="html",
            file_size=100,
            source_path="documents/test.html",
            status="deleting",
        )
        session.add(document)
        await session.flush()
        task = new_task(document, context["runtime"])
        session.add(task)
        await session.commit()
        with pytest.raises(ParseError):
            await stage(session, task, document, "embedding", 30)
        await session.rollback()
        await session.refresh(document)
        assert document.status == "deleting"


async def test_process_task_missing_models_persists_failure_and_retry_state(context):
    from app.services.runtime_config import build_settings, configuration_values, index_fingerprint

    values = {**configuration_values(context["runtime"]), "model_api_base_url": ""}
    candidate = context["settings"].model_copy(update=values)
    settings = build_settings(
        context["settings"],
        configuration_values(candidate),
        index_fingerprint(candidate),
        context["runtime"].configuration_id,
    )
    storage = LocalStorage(settings)
    storage.write_json(
        "documents/failure.html", "<html><p>Redis caching avoids expensive database reads.</p></html>"
    )
    engine = make_engine(settings)
    doc_id = None
    try:
        async with make_session_factory(engine)() as session:
            document = Document(
                doc_id="doc_" + uuid4().hex,
                title="Failure test",
                original_filename="failure.html",
                file_type="html",
                file_size=100,
                source_path="documents/failure.html",
            )
            session.add(document)
            await session.flush()
            doc_id = document.id
            task = new_task(document, settings)
            session.add(task)
            await session.commit()
            task_id = task.id
        await process_task(task_id, settings)
        async with make_session_factory(engine)() as session:
            task = await session.get(ProcessingTask, task_id)
            document = await session.get(Document, doc_id)
            assert task.status == "failed"
            assert task.attempts == 1
            assert "尚未配置" in task.error_message
            assert document.status == "failed"
    finally:
        if doc_id is not None:
            async with make_session_factory(engine)() as session:
                await session.execute(delete(Document).where(Document.id == doc_id))
                await session.commit()
        await engine.dispose()


async def test_known_tag_candidates_exclude_category_names_and_are_reproducible(context):
    """A category root offered as a candidate comes straight back as a "tag"; an unordered cap makes the model
    see an arbitrary subset once approved tags exceed it. Both are silent tagging drift."""
    from app.services.pipeline import known_tag_candidates

    async with context["sessions"]() as session:
        paths = list(await session.scalars(select(Category.path)))
        assert paths
        root = paths[0].split("/")[0]
        session.add_all(
            [
                Tag(
                    name=root, normalized_name=root.casefold(), review_status="approved", auto_generated=True
                ),
                Tag(
                    name="幂等设计",
                    normalized_name="幂等设计",
                    review_status="approved",
                    auto_generated=True,
                ),
                Tag(
                    name="草稿标签", normalized_name="草稿标签", review_status="pending", auto_generated=True
                ),
            ]
        )
        await session.commit()

        candidates = await known_tag_candidates(session, paths)
        assert "幂等设计" in candidates
        assert root not in candidates
        assert "草稿标签" not in candidates
        assert candidates == await known_tag_candidates(session, paths)


def test_the_gate_cannot_be_configured_above_the_llm_only_confidence():
    """0.9 sat here once and quietly switched the whole tag feature off; the bound is now the type's job."""
    from pydantic import ValidationError

    from app.core.config import LLM_ONLY_CONFIDENCE, Settings

    assert Settings(tag_auto_approve_confidence=LLM_ONLY_CONFIDENCE).tag_auto_approve_confidence == 0.7
    with pytest.raises(ValidationError):
        Settings(tag_auto_approve_confidence=0.9)


def test_the_default_auto_approve_gate_does_not_outrank_the_llm_only_confidence():
    """A gate above LLM_ONLY_CONFIDENCE silently disconnects tags from retrieval, as 0.9 against 0.7 once did."""
    from app.core.config import Settings
    from app.services.pipeline import LLM_ONLY_CONFIDENCE

    assert Settings().tag_auto_approve_confidence == LLM_ONLY_CONFIDENCE
