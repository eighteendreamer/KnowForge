import asyncio
import html
import json
import re
import time

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cached_search, revision, search_cache_key
from app.core.config import Settings
from app.core.metrics import SEARCH_CACHE
from app.core.model_client import ModelGateway
from app.models import Category, Chunk, ChunkTag, Document, Tag
from app.schemas.search import SearchInput
from app.services.retrieval.bm25_encoder import terms
from app.services.retrieval.filters import database_filters, vector_filters
from app.services.retrieval.fuzzy_search import fuzzy_search
from app.services.retrieval.intent import detect_strategy
from app.services.retrieval.normalize import to_simplified
from app.services.retrieval.query_rewrite import rewrite_query
from app.services.retrieval.rrf import Candidate, reciprocal_rank_fusion
from app.services.storage.qdrant_store import QdrantStore


def highlight_text(content: str, query: str) -> str:
    keywords = sorted(set(terms(query)), key=len, reverse=True)[:30]
    if not keywords:
        return html.escape(content)
    pattern = re.compile("|".join(re.escape(keyword) for keyword in keywords), re.IGNORECASE)
    output, end = [], 0
    for match in pattern.finditer(content):
        output.extend([html.escape(content[end : match.start()]), "<em>" + html.escape(match[0]) + "</em>"])
        end = match.end()
    output.append(html.escape(content[end:]))
    return "".join(output)


MAX_OVERLAP_CHARS = 200


def group_adjacent(ranked: list[tuple[str, int, int]], merge: bool) -> list[list[str]]:
    """方案 3.5.1 后处理去重：同文档且序号相邻的命中并成一组，名次最好的分块代表这一组。"""
    groups: list[list[str]] = []
    placed: dict[tuple[int, int], int] = {}
    for chunk_id, doc_id, chunk_index in ranked:
        if not merge:
            groups.append([chunk_id])
            continue
        for neighbor in (chunk_index - 1, chunk_index + 1):
            owner = placed.get((doc_id, neighbor))
            if owner is not None:
                groups[owner].append(chunk_id)
                placed[(doc_id, chunk_index)] = owner
                break
        else:
            placed[(doc_id, chunk_index)] = len(groups)
            groups.append([chunk_id])
    return groups


def join_overlapped(texts: list[str]) -> str:
    """相邻分块本身带重叠区，拼接前去掉最长公共的尾-头片段，避免同一段话出现两次。"""
    joined = texts[0]
    for text in texts[1:]:
        overlap = 0
        for size in range(min(len(joined), len(text), MAX_OVERLAP_CHARS), 0, -1):
            if joined[-size:] == text[:size]:
                overlap = size
                break
        joined += text[overlap:]
    return joined


async def search(
    session: AsyncSession,
    body: SearchInput,
    settings: Settings,
    gateway: ModelGateway,
    store: QdrantStore,
    redis: Redis,
    public_only: bool = True,
) -> dict:
    started = time.perf_counter()
    # 方案 3.5.1：繁简归一与意图选路都在召回之前定下来，之后一切以选定模式为准（含缓存计数与响应里的 search_type_used）。
    normalized = to_simplified(body.query)
    mode = detect_strategy(normalized) if body.search_type == "auto" else body.search_type
    conditions = await database_filters(session, body.filters, public_only)
    current_revision = await revision(session)
    cache_key = search_cache_key(
        settings.redis_prefix,
        body.model_dump(),
        current_revision,
        str(settings.configuration_id) + str(settings.index_fingerprint),
        store.collection,
        "public" if public_only else "admin",
    )
    cached = await cached_search(redis, cache_key)
    if cached is not None:
        ids = [item["id"] for item in cached["results"]]
        valid = set(
            await session.scalars(
                select(Chunk.chunk_id)
                .join(Document, Chunk.doc_id == Document.id)
                .where(*conditions, Chunk.chunk_id.in_(ids))
            )
        )
        if valid == set(ids) and await revision(session) == current_revision:
            SEARCH_CACHE.labels(
                cached.get("search_type_used", mode), "public" if public_only else "admin", "hit"
            ).inc()
            cached["took_ms"] = int((time.perf_counter() - started) * 1000)
            return cached
    SEARCH_CACHE.labels(mode, "public" if public_only else "admin", "miss").inc()
    # End the read transaction so no pooled connection is held across the remote calls below.
    await session.commit()
    query = (
        await rewrite_query(normalized, gateway)
        if body.options.query_rewrite and mode in {"semantic", "hybrid"}
        else normalized
    )
    vector = (await gateway.embed([query], "online"))[0] if mode in {"semantic", "hybrid"} else None
    filters = await vector_filters(session, body.filters, public_only)
    # Qdrant is a remote call too: close the read transaction that loaded the filter ids.
    await session.commit()
    rankings: list[list[Candidate]] = []
    requests = []
    if vector is not None:
        requests.append(store.dense(vector, filters))
    if mode in {"keyword", "hybrid"}:
        requests.append(store.keyword(query, filters))
    for points in await asyncio.gather(*requests):
        rankings.append(
            [Candidate(point.payload["chunk_id"], point.score) for point in points if point.payload]
        )
    if mode in {"fuzzy", "hybrid"}:
        rankings.append(await fuzzy_search(session, query, conditions))
    candidates = reciprocal_rank_fusion(rankings) if mode == "hybrid" else (rankings[0] if rankings else [])
    ids = [candidate.chunk_id for candidate in candidates]
    records = await session.execute(
        select(Chunk, Document)
        .join(Document, Chunk.doc_id == Document.id)
        .where(*conditions, Chunk.chunk_id.in_(ids))
    )
    rows = {chunk.chunk_id: (chunk, document) for chunk, document in records}
    candidates = [candidate for candidate in candidates if candidate.chunk_id in rows]
    use_rerank = body.options.rerank and settings.rerank_enabled
    score_type: str = "rrf" if mode == "hybrid" else mode
    if use_rerank and candidates:
        await session.commit()
        ranked = await gateway.rerank(
            query, [rows[candidate.chunk_id][0].text_with_context for candidate in candidates]
        )
        candidates = [Candidate(candidates[item.index].chunk_id, item.relevance_score) for item in ranked]
        score_type = "rerank"
    records = await session.execute(
        select(Chunk, Document)
        .join(Document, Chunk.doc_id == Document.id)
        .where(*conditions, Chunk.chunk_id.in_(ids))
        .execution_options(populate_existing=True)
    )
    rows = {chunk.chunk_id: (chunk, document) for chunk, document in records}
    candidates = [candidate for candidate in candidates if candidate.chunk_id in rows]
    total = len(candidates)
    categories = {category.id: category.path for category in await session.scalars(select(Category))}
    associations = await session.execute(
        select(ChunkTag.chunk_id, Tag.name)
        .join(Tag, Tag.id == ChunkTag.tag_id)
        .join(Chunk, Chunk.id == ChunkTag.chunk_id)
        .where(Chunk.chunk_id.in_(ids), Tag.review_status == "approved")
    )
    tag_map: dict[int, list[str]] = {}
    for chunk_id, name in associations:
        tag_map.setdefault(chunk_id, []).append(name)
    scores = {candidate.chunk_id: candidate.score for candidate in candidates}
    groups = group_adjacent(
        [
            (candidate.chunk_id, rows[candidate.chunk_id][0].doc_id, rows[candidate.chunk_id][0].chunk_index)
            for candidate in candidates
        ],
        body.options.merge_adjacent,
    )
    results = []
    for group in groups[: body.top_k]:
        chunk, document = rows[group[0]]
        pieces = [rows[chunk_id][0] for chunk_id in group]
        content = join_overlapped([piece.text for piece in pieces])
        category_id = document.manual_category_id or chunk.category_id or document.auto_category_id
        item = {
            "id": chunk.chunk_id,
            "doc_id": document.doc_id,
            "title": document.title,
            "content": content,
            "score": scores[chunk.chunk_id],
            "score_type": score_type,
            "score_calibrated": False,
            "source": document.original_filename,
            "page": chunk.page_start,
        }
        if len(group) > 1:
            item["merged_ids"] = group[1:]
        if body.options.highlight:
            item["highlight"] = highlight_text(content, query)
        if body.options.include_metadata:
            item.update(
                {
                    "section": " > ".join(chunk.section_path),
                    "tags": tag_map.get(chunk.id, []),
                    "category": categories.get(category_id, "") if category_id is not None else "",
                    "difficulty": chunk.difficulty,
                    "page_end": pieces[-1].page_end,
                    "url": f"/v1/knowledge/documents/{document.doc_id}/file",
                }
            )
        results.append(item)
    data = {
        "query": body.query,
        "query_rewritten": query if query != normalized else None,
        "search_type_used": mode,
        "total": total,
        "results": results,
        "suggested_tags": sorted(
            {tag for values in tag_map.values() for tag in values} - set(body.filters.tags)
        )[:10],
        "took_ms": int((time.perf_counter() - started) * 1000),
    }
    await redis.set(cache_key, json.dumps(data, ensure_ascii=False), ex=settings.cache_ttl_seconds)
    return data
