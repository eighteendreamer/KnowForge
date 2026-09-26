from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings
from app.schemas.documents import ChunkData
from app.services.retrieval.bm25_encoder import bm25_vector, terms
from app.services.runtime_config import index_fingerprint
from app.services.storage.qdrant_store import FINGERPRINT_KEY, QdrantStore


def test_bm25_query_does_not_double_apply_idf():
    assert "redis" in terms("Redis 缓存穿透")
    query = bm25_vector("Redis Redis", query=True)
    assert query.values == [1.0]
    assert bm25_vector("Redis Redis").values[0] > 1


async def test_upsert_splits_large_documents_into_multiple_writes():
    settings = Settings().model_copy(
        update={
            "qdrant_collection": "knowforge_test_" + uuid4().hex,
            "index_fingerprint": index_fingerprint(Settings()),
        }
    )
    client = AsyncQdrantClient(url=settings.qdrant_url)
    store = QdrantStore(client, settings)
    batches: list[int] = []
    real_upsert = client.upsert

    async def record(collection_name, points, **kwargs):
        batches.append(len(points))
        return await real_upsert(collection_name=collection_name, points=points, **kwargs)

    try:
        await store.ensure_collection()
        client.upsert = record  # type: ignore[method-assign]
        chunks = [
            ChunkData(
                chunk_id="chunk_" + uuid4().hex,
                doc_id="doc_batch",
                chunk_index=index,
                section_path=["批量"],
                text=f"Redis 缓存穿透方案 {index}",
                text_with_context=f"Redis 缓存穿透方案 {index}",
                page_start=1,
                page_end=None,
                char_count=20,
                token_count=8,
                element_types=["NarrativeText"],
                metadata={"tags": ["Redis"], "source": "batch.pdf"},
            )
            for index in range(70)
        ]
        await store.upsert(
            chunks, [[1.0 if index == 0 else 0.0 for index in range(4096)] for _ in chunks], True
        )
        assert batches == [64, 6]
        assert (await client.count(store.collection, exact=True)).count == 70
    finally:
        client.upsert = real_upsert  # type: ignore[method-assign]
        await client.delete_collection(store.collection)
        await client.close()


async def test_real_qdrant_dense_sparse_filters_and_idempotent_delete():
    settings = Settings().model_copy(
        update={
            "qdrant_collection": "knowforge_test_" + uuid4().hex,
            "index_fingerprint": index_fingerprint(Settings()),
        }
    )
    client = AsyncQdrantClient(url=settings.qdrant_url)
    store = QdrantStore(client, settings)
    try:
        await store.ensure_collection()
        await store.ensure_collection()
        chunks = [
            ChunkData(
                chunk_id="chunk_" + uuid4().hex,
                doc_id="doc_test",
                chunk_index=index,
                section_path=["Redis"],
                text=text,
                text_with_context=text,
                page_start=1,
                page_end=1,
                char_count=len(text),
                token_count=10,
                element_types=["NarrativeText"],
                metadata={"tags": [tag], "source": "sample.pdf"},
            )
            for index, (text, tag) in enumerate(
                [("Redis 缓存穿透使用布隆过滤器", "Redis"), ("Kafka 消息重试与消费者提交", "Kafka")]
            )
        ]
        vectors = [
            [1.0 if index == 0 else 0.0 for index in range(4096)],
            [1.0 if index == 1 else 0.0 for index in range(4096)],
        ]
        await store.upsert(chunks, vectors, True)
        await store.upsert(chunks, vectors, True)
        assert (await client.count(store.collection, exact=True)).count == 2
        filters = models.Filter(
            must=[models.FieldCondition(key="is_public", match=models.MatchValue(value=True))]
        )
        keyword = await store.keyword("Redis", filters)
        assert len(keyword) == 1
        assert keyword[0].payload["tags"] == ["Redis"]
        dense = await store.dense(vectors[0], filters, 1)
        assert dense[0].payload["chunk_id"] == chunks[0].chunk_id
        private_filter = models.Filter(
            must=[models.FieldCondition(key="is_public", match=models.MatchValue(value=False))]
        )
        assert not await store.keyword("Redis", private_filter)
        mismatch = QdrantStore(client, settings.model_copy(update={"embedding_dimension": 1024}))
        with pytest.raises(ValueError, match="Schema"):
            await mismatch.ensure_collection()
        other = settings.model_copy(
            update={
                "qdrant_collection": "knowforge_test_" + uuid4().hex,
                "index_fingerprint": index_fingerprint(
                    settings.model_copy(update={"embedding_model": "other"})
                ),
            }
        )
        other_store = QdrantStore(client, other)
        await other_store.ensure_collection()
        await other_store.upsert(chunks[:1], [vectors[0]], True)
        hijacked = settings.model_copy(update={"qdrant_collection": other.qdrant_collection})
        with pytest.raises(ValueError, match="禁止混用向量"):
            await QdrantStore(client, hijacked).ensure_collection()
        assert (await client.get_collection(other.qdrant_collection)).config.metadata[FINGERPRINT_KEY] == (
            other.index_fingerprint
        )
        await client.delete_collection(other.qdrant_collection)
        await store.delete_document("doc_test")
        await store.delete_document("doc_test")
        assert (await client.count(store.collection, exact=True)).count == 0
    finally:
        await client.delete_collection(store.collection)
        await client.close()
