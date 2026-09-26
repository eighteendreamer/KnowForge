from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from knowforge_sdk import AsyncKnowForge, KnowForgeError
from sqlalchemy import select

from app.core.cache import bump_revision
from app.models import Category, Chunk, ChunkTag, Document, Tag
from app.schemas.documents import DocumentAST, Element
from app.schemas.search import SearchInput, SearchOptions
from app.services.indexing import chunk_data
from app.services.retrieval.rrf import Candidate, reciprocal_rank_fusion
from app.services.retrieval.search_service import highlight_text, search
from app.services.storage.file_storage import LocalStorage
from app.services.storage.qdrant_store import QdrantStore


@pytest.fixture
async def searchable(context):
    async with context["sessions"]() as session:
        category = await session.scalar(select(Category).where(Category.path == "后端开发/缓存/Redis"))
        tag = Tag(name="Redis", normalized_name="redis", review_status="approved")
        session.add(tag)
        await session.flush()
        documents = []
        for public in [True, False]:
            document = Document(
                doc_id="doc_" + uuid4().hex,
                title="Redis guide",
                original_filename="guide.html",
                file_type="html",
                file_size=100,
                source_path="documents/guide.html",
                ast_path="ast/guide.json",
                status="ready",
                total_chunks=1,
                is_public=public,
                auto_category_id=category.id,
            )
            session.add(document)
            await session.flush()
            point_id = uuid4()
            row = Chunk(
                chunk_id="chunk_" + point_id.hex,
                doc_id=document.id,
                chunk_index=0,
                section_path=["Redis"],
                text="Redis caching uses expiration and invalidation.",
                text_with_context="Redis caching uses expiration and invalidation.",
                char_count=50,
                token_count=12,
                element_types=["NarrativeText"],
                meta={},
                category_id=category.id,
                difficulty="中级",
                qdrant_point_id=point_id,
            )
            session.add(row)
            await session.flush()
            session.add(ChunkTag(chunk_id=row.id, tag_id=tag.id, source="manual", confidence=1))
            documents.append(document)
        await session.commit()
        store = QdrantStore(context["app"].state.qdrant, context["runtime"])
        for document in documents:
            await store.upsert(await chunk_data(session, document), [[1.0] + [0.0] * 4095], True)
        ast = DocumentAST(
            doc_id=documents[0].doc_id,
            title="Redis guide",
            source_type="html",
            source_path="documents/guide.html",
            upload_time=datetime.now(UTC),
            uploader="admin",
            elements=[Element(type="NarrativeText", text="Redis caching uses expiration and invalidation.")],
            stats={"total_elements": 1, "total_pages": None, "total_chars": 50},
        )
        storage = LocalStorage(context["settings"])
        storage.write_json("ast/guide.json", ast.model_dump_json())
        storage.write_json("documents/guide.html", "<html><p>Redis caching</p></html>")
    created = await context["client"].post(
        "/v1/admin/api-keys", headers=context["admin_headers"], json={"name": "search"}
    )
    return {"documents": documents, "headers": {"Authorization": "Bearer " + created.json()["data"]["key"]}}


@pytest.mark.parametrize("mode", ["keyword", "fuzzy"])
async def test_search_enforces_database_visibility_despite_stale_vector_payload(context, searchable, mode):
    response = await context["client"].post(
        "/v1/knowledge/search",
        headers=searchable["headers"],
        json={"query": "Redis", "search_type": mode, "options": {"rerank": False}},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["results"]) == 1
    assert data["results"][0]["doc_id"] == searchable["documents"][0].doc_id
    assert data["results"][0]["source"] == "guide.html"
    assert data["results"][0]["tags"] == ["Redis"]
    assert data["results"][0]["section"] == "Redis"
    assert "<em>Redis</em>" in data["results"][0]["highlight"]


async def test_auto_search_type_chooses_by_intent_and_reports_the_resolved_mode(context, searchable):
    client = context["client"]
    response = await client.post(
        "/v1/knowledge/search",
        headers=searchable["headers"],
        json={"query": "Redis caching", "search_type": "auto", "options": {"rerank": False}},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["search_type_used"] == "keyword"
    assert data["results"]
    assert data["results"][0]["score_type"] == "keyword"


class MatchingVectorGateway:
    """返回与两条已入库向量完全相同的查询向量，因此只有数据库可见性判断能把私有分块挡掉。"""

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts, priority="batch"):
        self.calls += 1
        return [[1.0] + [0.0] * 4095 for _ in texts]


async def _run(context, searchable, mode):
    gateway = MatchingVectorGateway()
    store = QdrantStore(context["app"].state.qdrant, context["runtime"])
    async with context["sessions"]() as session:
        body = SearchInput(
            query="Redis caching",
            search_type=mode,
            top_k=10,
            options=SearchOptions(rerank=False, query_rewrite=False),
        )
        data = await search(session, body, context["runtime"], gateway, store, context["app"].state.redis)
    return data, gateway


@pytest.mark.parametrize(
    "requested,used,embed_calls",
    [("semantic", "semantic", 1), ("hybrid", "hybrid", 1), ("auto", "keyword", 0)],
)
async def test_vector_backed_modes_hide_private_chunks_even_when_the_vectors_match(
    context, searchable, requested, used, embed_calls
):
    """方案 8.3 要求"私有文档不得进入任意召回路径"；这里补上原先只覆盖 keyword/fuzzy 的两条向量路径。"""
    data, gateway = await _run(context, searchable, requested)
    visible = {item["doc_id"] for item in data["results"]}
    assert visible == {searchable["documents"][0].doc_id}
    assert data["search_type_used"] == used
    assert gateway.calls == embed_calls


async def test_deleting_documents_leave_every_recall_path(context, searchable):
    client, headers = context["client"], searchable["headers"]
    baseline = await client.post(
        "/v1/knowledge/search",
        headers=headers,
        json={"query": "Redis caching", "search_type": "keyword", "options": {"rerank": False}},
    )
    assert baseline.json()["data"]["results"]
    async with context["sessions"]() as session:
        for document in searchable["documents"]:
            row = await session.get(Document, document.id)
            row.status = "deleting"
            await session.commit()
        await bump_revision(session)
        await session.commit()
    for mode in ["keyword", "fuzzy"]:
        response = await client.post(
            "/v1/knowledge/search",
            headers=headers,
            json={"query": "Redis caching", "search_type": mode, "options": {"rerank": False}},
        )
        assert response.json()["data"]["results"] == [], mode
    lookup = await client.post(
        "/v1/knowledge/lookup", headers=headers, json={"doc_id": searchable["documents"][0].doc_id}
    )
    assert lookup.json()["code"] != 0


async def test_tag_approval_invalidates_the_cached_search_that_hid_the_tag(context, searchable):
    """方案 8.3 点名"标签审核后缓存未失效"：过审的标签必须立刻在检索结果里可见，而不是继续吃旧缓存。"""
    client = context["client"]
    body = {"query": "Redis caching", "search_type": "keyword", "options": {"rerank": False}}
    async with context["sessions"]() as session:
        chunk = await session.scalar(select(Chunk).where(Chunk.doc_id == searchable["documents"][0].id))
        hidden = Tag(name="布隆过滤器", normalized_name="布隆过滤器", review_status="pending")
        session.add(hidden)
        await session.flush()
        session.add(ChunkTag(chunk_id=chunk.id, tag_id=hidden.id, source="auto", confidence=0.8))
        await bump_revision(session)
        await session.commit()
        tag_id = hidden.id

    first = await client.post("/v1/knowledge/search", headers=searchable["headers"], json=body)
    assert first.json()["data"]["results"][0]["tags"] == ["Redis"]

    reviewed = await client.post(
        "/v1/admin/tags/batch-review",
        headers=context["admin_headers"],
        json={"ids": [tag_id], "review_status": "approved"},
    )
    assert reviewed.status_code == 200

    second = await client.post("/v1/knowledge/search", headers=searchable["headers"], json=body)
    assert "布隆过滤器" in second.json()["data"]["results"][0]["tags"]


async def test_cache_invalidates_on_visibility_change(context, searchable):
    client = context["client"]
    body = {"query": "Redis", "search_type": "keyword", "options": {"rerank": False}}
    first = await client.post("/v1/knowledge/search", headers=searchable["headers"], json=body)
    assert first.json()["data"]["results"]
    async with context["sessions"]() as session:
        document = await session.get(Document, searchable["documents"][0].id)
        document.is_public = False
        await bump_revision(session)
        await session.commit()
    second = await client.post("/v1/knowledge/search", headers=searchable["headers"], json=body)
    assert second.status_code == 200
    assert second.json()["data"]["results"] == []


async def test_filters_and_lookup_are_consistent(context, searchable):
    client = context["client"]
    body = {
        "query": "Redis",
        "search_type": "keyword",
        "filters": {"category": "后端开发/缓存", "tags": ["Redis"]},
        "options": {"rerank": False},
    }
    response = await client.post("/v1/knowledge/search", headers=searchable["headers"], json=body)
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]["results"]) == 1
    body["filters"]["exclude_tags"] = ["Redis"]
    excluded = await client.post("/v1/knowledge/search", headers=searchable["headers"], json=body)
    assert excluded.json()["data"]["results"] == []
    for index, expected in [(0, 200), (1, 404)]:
        result = await client.post(
            "/v1/knowledge/lookup",
            headers=searchable["headers"],
            json={"doc_id": searchable["documents"][index].doc_id},
        )
        assert result.status_code == expected, result.text
        if index == 0:
            assert "Redis" in result.json()["data"]["content"]
            assert "source_path" not in result.text
    tags = await client.get("/v1/knowledge/tags", headers=searchable["headers"])
    assert tags.json()["data"]["tags"][0]["count"] == 1


@pytest.mark.parametrize("query", ["", " " * 10, "x" * 501])
async def test_invalid_search_query_uses_contract_code(context, searchable, query):
    result = await context["client"].post(
        "/v1/knowledge/search", headers=searchable["headers"], json={"query": query}
    )
    assert result.status_code == 400
    assert result.json()["code"] == 1002


def test_rrf_deduplicates_and_highlight_escapes_html():
    results = reciprocal_rank_fusion(
        [[Candidate("a", 1), Candidate("a", 0.5)], [Candidate("b", 1), Candidate("a", 0.8)]]
    )
    assert results[0].chunk_id == "a"
    assert results[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert "<script>" not in highlight_text("<script>alert(1)</script> Redis", "Redis")
    assert "<em>Redis</em>" in highlight_text("<script>alert(1)</script> Redis", "Redis")


async def test_python_sdk_against_real_storage_api(context, searchable):
    key = searchable["headers"]["Authorization"].removeprefix("Bearer ")
    async with AsyncKnowForge(
        "http://localhost/v1", key, transport=httpx.ASGITransport(app=context["app"])
    ) as sdk:
        result = await sdk.search("Redis", search_type="keyword", options={"rerank": False})
        assert len(result["results"]) == 1
        doc_id = result["results"][0]["doc_id"]
        assert "Redis" in (await sdk.lookup(doc_id))["content"]
        assert b"Redis" in await sdk.source(doc_id)
        assert (await sdk.tags())["tags"][0]["name"] == "Redis"
        assert (await sdk.categories())["tree"]
        for operation in (sdk.lookup, sdk.source):
            with pytest.raises(KnowForgeError) as error:
                await operation(searchable["documents"][1].doc_id)
            assert error.value.status == 404
        with pytest.raises(KnowForgeError) as error:
            await sdk.search(" ")
        assert error.value.code == 1002
