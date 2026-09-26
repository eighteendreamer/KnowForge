import asyncio
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.deps import Admin, KnowledgeKey, Runtime, Session
from app.api.public_docs import (
    CHANGELOG,
    ERROR_CODES,
    PERFORMANCE,
    QUICKSTART,
    RATE_LIMITS,
    SDKS,
    public_endpoints,
)
from app.core.errors import AppError, success
from app.core.privacy import redact_query
from app.models import Category, Chunk, ChunkTag, Document, Tag
from app.schemas.documents import DocumentAST
from app.schemas.search import LookupInput, SearchInput
from app.services.retrieval.filters import category_scope
from app.services.retrieval.search_service import search
from app.services.storage.file_storage import LocalStorage

router = APIRouter(prefix="/v1/knowledge", tags=["开放知识检索"])
admin_router = APIRouter(prefix="/v1/admin", tags=["检索测试台"])
PUBLIC_PREFIX = "/v1/knowledge"


@router.get("/api-docs")
async def api_docs(request: Request, path: str | None = Query(None, max_length=200)):
    """Discovery endpoint for integrators: only the publicly exposed knowledge API is listed."""
    base_url = str(request.base_url).rstrip("/") + PUBLIC_PREFIX
    schema = request.app.openapi()
    endpoints = public_endpoints(schema, base_url)
    if path:
        needle = path.strip("/")
        endpoints = [item for item in endpoints if needle in item["path"].strip("/")]
    return success(
        {
            "base_url": base_url,
            "version": schema["info"]["version"],
            "auth": "Authorization: Bearer <API Key>，作用域 knowledge:read",
            "envelope": {
                "success": '{"code":0,"message":"success","data":{...}}',
                "error": '{"code":<非零>,"message":"..."}',
            },
            "error_codes": ERROR_CODES,
            "rate_limits": RATE_LIMITS,
            "performance": PERFORMANCE,
            "quickstart": QUICKSTART,
            "sdks": SDKS,
            "changelog": CHANGELOG,
            "endpoints": endpoints,
        }
    )


@router.post("/search")
async def knowledge_search(
    body: SearchInput, request: Request, session: Session, key: KnowledgeKey, runtime: Runtime
):
    request.state.search_query = redact_query(body.query)
    request.state.search_type = body.search_type
    request.state.top_k = body.top_k
    data = await search(
        session,
        body,
        runtime.settings,
        runtime.gateway,
        runtime.store,
        request.app.state.redis,
    )
    # search_type=auto 时调用日志要留下实际生效的模式，而不是客户端传来的字面值。
    request.state.search_type = data["search_type_used"]
    request.state.result_count = len(data["results"])
    return success(data)


@admin_router.post("/search")
async def test_search(body: SearchInput, request: Request, session: Session, user: Admin, runtime: Runtime):
    return success(
        await search(
            session,
            body,
            runtime.settings,
            runtime.gateway,
            runtime.store,
            request.app.state.redis,
            public_only=False,
        )
    )


@router.post("/lookup")
async def lookup(body: LookupInput, request: Request, session: Session, key: KnowledgeKey, runtime: Runtime):
    document = await session.scalar(
        select(Document).where(
            Document.doc_id == body.doc_id, Document.status == "ready", Document.is_public.is_(True)
        )
    )
    if document is None:
        raise AppError(404, 1001, "文档不存在或不可访问")
    if body.page_start and (
        document.total_pages is None
        or body.page_start > document.total_pages
        or (body.page_end and body.page_end > document.total_pages)
    ):
        raise AppError(400, 1001, "页码超出文档范围")
    if not document.ast_path:
        raise AppError(503, 5002, "文档中间结果尚未就绪")
    content = await asyncio.to_thread(
        LocalStorage(runtime.settings).resolve(document.ast_path).read_text, encoding="utf-8"
    )
    ast = DocumentAST.model_validate(json.loads(content))
    elements = [
        element
        for element in ast.elements
        if body.page_start is None
        or (
            element.page is not None and body.page_start <= element.page <= (body.page_end or body.page_start)
        )
    ]
    data = {
        "doc_id": document.doc_id,
        "title": document.title,
        "source": document.original_filename,
        "content": "\n\n".join(element.text for element in elements),
        "elements": [element.model_dump() for element in elements],
    }
    if body.include_chunks:
        statement = select(Chunk).where(Chunk.doc_id == document.id)
        if body.page_start:
            statement = statement.where(
                Chunk.page_end >= body.page_start, Chunk.page_start <= (body.page_end or body.page_start)
            )
        chunks = await session.scalars(statement.order_by(Chunk.chunk_index))
        data["chunks"] = [
            {
                "id": chunk.chunk_id,
                "content": chunk.text,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
            }
            for chunk in chunks
        ]
    return success(data)


@router.get("/tags")
async def knowledge_tags(
    session: Session, key: KnowledgeKey, category: str | None = Query(None, max_length=500)
):
    conditions = [Tag.review_status == "approved", Document.status == "ready", Document.is_public.is_(True)]
    if category:
        categories = await category_scope(session, category)
        conditions.append(
            func.coalesce(Document.manual_category_id, Chunk.category_id, Document.auto_category_id).in_(
                [row.id for row in categories]
            )
        )
    rows = await session.execute(
        select(Tag.name, func.count(func.distinct(Document.id)))
        .join(ChunkTag, ChunkTag.tag_id == Tag.id)
        .join(Chunk, ChunkTag.chunk_id == Chunk.id)
        .join(Document, Chunk.doc_id == Document.id)
        .where(*conditions)
        .group_by(Tag.id)
        .order_by(Tag.name)
    )
    return success({"tags": [{"name": name, "count": count, "category": category} for name, count in rows]})


@router.get("/categories")
async def knowledge_categories(session: Session, key: KnowledgeKey):
    categories = list(await session.scalars(select(Category).order_by(Category.sort_order, Category.id)))
    documents = await session.execute(
        select(Document.id, func.coalesce(Document.manual_category_id, Document.auto_category_id)).where(
            Document.status == "ready", Document.is_public.is_(True)
        )
    )
    members: dict[int, set[int]] = {category.id: set() for category in categories}
    by_id = {category.id: category for category in categories}
    for doc_id, category_id in documents:
        visited = set()
        while category_id is not None and category_id in by_id and category_id not in visited:
            visited.add(category_id)
            members[category_id].add(doc_id)
            category_id = by_id[category_id].parent_id
    nodes: dict[int, dict] = {
        category.id: {
            "name": category.name,
            "path": category.path,
            "count": len(members[category.id]),
            "children": [],
        }
        for category in categories
    }
    tree = []
    for category in categories:
        if category.parent_id in nodes:
            nodes[category.parent_id]["children"].append(nodes[category.id])
        else:
            tree.append(nodes[category.id])
    return success({"tree": tree})


@router.get("/documents/{doc_id}/file")
async def source_file(doc_id: str, request: Request, session: Session, key: KnowledgeKey, runtime: Runtime):
    document = await session.scalar(
        select(Document).where(
            Document.doc_id == doc_id, Document.is_public.is_(True), Document.status == "ready"
        )
    )
    if document is None:
        raise AppError(404, 1001, "文档不存在或不可访问")
    path = LocalStorage(runtime.settings).resolve(document.source_path)
    return FileResponse(path, media_type="application/octet-stream", filename=document.original_filename)
