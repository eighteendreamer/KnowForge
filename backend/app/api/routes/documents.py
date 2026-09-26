import asyncio
from typing import Annotated
from uuid import uuid4

import nh3
from bs4 import UnicodeDammit
from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, select

from app.api.deps import Admin, Runtime, Session
from app.core.cache import bump_revision
from app.core.errors import AppError, success
from app.models import Category, Chunk, ChunkTag, Document, DocumentTag, Tag
from app.schemas.documents import DocumentPatch, UploadComplete
from app.services.audit import record_audit
from app.services.pipeline import new_task
from app.services.storage.file_storage import LocalStorage
from app.services.task_queue import dispatch, require_consumer

router = APIRouter(prefix="/v1/admin/documents", tags=["文档管理"])
LIST_TAG_LIMIT = 8


def document_view(document: Document, tags: list[str] | None = None, tag_total: int | None = None) -> dict:
    return {
        "id": document.doc_id,
        "doc_id": document.doc_id,
        "title": document.title,
        "source": document.original_filename,
        "file_type": document.file_type,
        "file_size": document.file_size,
        "status": document.status,
        "upload_time": document.upload_time,
        "total_pages": document.total_pages,
        "total_chunks": document.total_chunks,
        "category_id": document.manual_category_id or document.auto_category_id,
        "is_public": document.is_public,
        "parse_error": document.parse_error,
        "tags": tags or [],
        "tag_total": tag_total if tag_total is not None else len(tags or []),
    }


async def tag_summaries(session, doc_ids: list[int]) -> dict[int, tuple[list[str], int]]:
    """Rank each document's tags by manual curation then chunk coverage, and count them all."""
    if not doc_ids:
        return {}
    hits: dict[tuple[int, str], int] = {}
    for doc_id, name, count in await session.execute(
        select(Chunk.doc_id, Tag.name, func.count())
        .join(ChunkTag, ChunkTag.chunk_id == Chunk.id)
        .join(Tag, Tag.id == ChunkTag.tag_id)
        .where(Chunk.doc_id.in_(doc_ids))
        .group_by(Chunk.doc_id, Tag.name)
    ):
        hits[(doc_id, name)] = count
    ranked: dict[int, list[tuple[int, int, str]]] = {}
    for doc_id, name, source in await session.execute(
        select(DocumentTag.doc_id, Tag.name, DocumentTag.source)
        .join(Tag, Tag.id == DocumentTag.tag_id)
        .where(DocumentTag.doc_id.in_(doc_ids))
    ):
        ranked.setdefault(doc_id, []).append(
            (0 if source == "manual" else 1, -hits.get((doc_id, name), 0), name)
        )
    return {
        doc_id: ([name for _, _, name in sorted(entries)[:LIST_TAG_LIMIT]], len(entries))
        for doc_id, entries in ranked.items()
    }


async def require_document(session, doc_id: str) -> Document:
    document = await session.scalar(select(Document).where(Document.doc_id == doc_id))
    if document is None:
        raise AppError(404, 1001, "文档不存在")
    return document


async def register_document(
    request: Request,
    session,
    runtime,
    user,
    storage: LocalStorage,
    filename: str,
    file_type: str,
    key: str,
    size: int,
    category_id: int | None,
) -> dict:
    try:
        document = Document(
            doc_id="doc_" + uuid4().hex,
            title=filename.rsplit(".", 1)[0][:500],
            original_filename=filename[:500],
            file_type=file_type,
            file_size=size,
            source_path=key,
            uploader_id=user.id,
            manual_category_id=category_id,
        )
        session.add(document)
        await session.flush()
        task = new_task(document, runtime.settings)
        session.add(task)
        record_audit(session, request, user, "upload", "document", document.doc_id)
        await session.commit()
    except Exception:
        await asyncio.to_thread(storage.delete, key)
        raise
    await dispatch(request.app.state.celery, session, str(task.id))
    return {**document_view(document), "task_id": str(task.id)}


async def require_category(session, category_id: int | None) -> None:
    if category_id is not None and await session.get(Category, category_id) is None:
        raise AppError(400, 1001, "分类不存在")


@router.post("/upload")
async def upload_document(
    request: Request,
    session: Session,
    user: Admin,
    runtime: Runtime,
    file: Annotated[UploadFile, File()],
    category_id: Annotated[int | None, Form()] = None,
):
    await require_category(session, category_id)
    # Refuse before any bytes hit disk: a committed document whose task is never consumed cannot be re-submitted from the UI.
    await require_consumer(request.app.state.celery, session)
    storage = LocalStorage(runtime.settings)
    key, size, file_type = await storage.save_upload(file)
    filename = (file.filename or "document").replace("\\", "/").rsplit("/", 1)[-1]
    return success(
        await register_document(
            request, session, runtime, user, storage, filename, file_type, key, size, category_id
        )
    )


@router.post("/upload/chunk")
async def upload_document_chunk(
    request: Request,
    user: Admin,
    runtime: Runtime,
    upload_id: Annotated[str, Form()],
    part_number: Annotated[int, Form()],
    file: Annotated[UploadFile, File()],
):
    await LocalStorage(runtime.settings).save_part(upload_id, part_number, file)
    return success({"upload_id": upload_id, "part_number": part_number})


@router.post("/upload/complete")
async def upload_document_complete(
    body: UploadComplete,
    request: Request,
    session: Session,
    user: Admin,
    runtime: Runtime,
):
    await require_category(session, body.category_id)
    await require_consumer(request.app.state.celery, session)
    storage = LocalStorage(runtime.settings)
    filename = body.filename.replace("\\", "/").rsplit("/", 1)[-1]
    key, size, file_type = await storage.assemble(
        str(body.upload_id), filename, body.content_type, body.total_parts
    )
    return success(
        await register_document(
            request, session, runtime, user, storage, filename, file_type, key, size, body.category_id
        )
    )


@router.get("")
async def list_documents(
    session: Session,
    user: Admin,
    q: str = Query("", max_length=200),
    status: str | None = None,
    category_id: int | None = None,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = []
    if q:
        filters.append(Document.title.icontains(q, autoescape=True))
    if status:
        filters.append(Document.status == status)
    if category_id:
        filters.append(func.coalesce(Document.manual_category_id, Document.auto_category_id) == category_id)
    rows = await session.scalars(
        select(Document).where(*filters).order_by(Document.id.desc()).limit(limit).offset(offset)
    )
    total = await session.scalar(select(func.count()).select_from(Document).where(*filters))
    listed = list(rows)
    summaries = await tag_summaries(session, [document.id for document in listed])
    items = []
    for row in listed:
        names, total_tags = summaries.get(row.id, ([], 0))
        items.append(document_view(row, names, total_tags))
    return success({"items": items, "total": total})


@router.get("/{doc_id}")
async def document_detail(doc_id: str, session: Session, user: Admin):
    return success(document_view(await require_document(session, doc_id)))


@router.get("/{doc_id}/chunks")
async def document_chunks(
    doc_id: str,
    session: Session,
    user: Admin,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    document = await require_document(session, doc_id)
    chunks = await session.scalars(
        select(Chunk)
        .where(Chunk.doc_id == document.id)
        .order_by(Chunk.chunk_index)
        .offset(offset)
        .limit(limit)
    )
    return success(
        {
            "items": [
                {
                    "chunk_id": row.chunk_id,
                    "chunk_index": row.chunk_index,
                    "text": row.text,
                    "section_path": row.section_path,
                    "page_start": row.page_start,
                    "page_end": row.page_end,
                    "token_count": row.token_count,
                    "difficulty": row.difficulty,
                }
                for row in chunks
            ],
            "total": document.total_chunks,
        }
    )


SAFE_HTML_TAGS = {
    "p",
    "div",
    "span",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "table",
    "tr",
    "td",
    "th",
    "thead",
    "tbody",
    "pre",
    "code",
    "b",
    "strong",
    "em",
    "br",
    "blockquote",
}


def sanitize_html(raw: bytes) -> str:
    """Decode and strip an HTML document. Runs in a worker thread: both steps are CPU work."""
    content = UnicodeDammit(raw, is_html=True).unicode_markup
    if not content:
        raise AppError(400, 1001, "HTML 内容为空或编码无效")
    return nh3.clean(
        content,
        url_schemes=set(),
        attributes={},
        clean_content_tags={"script", "style", "head"},
        tags=SAFE_HTML_TAGS,
    )


@router.get("/{doc_id}/file")
async def preview_document(doc_id: str, request: Request, session: Session, user: Admin, runtime: Runtime):
    document = await require_document(session, doc_id)
    path = LocalStorage(runtime.settings).resolve(document.source_path)
    if not path.exists():
        raise AppError(404, 1001, "原始文件不存在")
    if document.file_type == "html":
        raw = await asyncio.to_thread(path.read_bytes)
        cleaned = await asyncio.to_thread(sanitize_html, raw)
        return HTMLResponse(
            '<!doctype html><html><head><meta charset="utf-8"></head><body>' + cleaned + "</body></html>",
            headers={
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "X-Frame-Options": "SAMEORIGIN",
            },
        )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=document.original_filename,
        content_disposition_type="inline",
    )


@router.patch("/{doc_id}")
async def patch_document(
    doc_id: str, body: DocumentPatch, request: Request, session: Session, user: Admin, runtime: Runtime
):
    document = await require_document(session, doc_id)
    if document.status == "deleting":
        raise AppError(400, 1001, "文档正在删除")
    if document.status == "ready":
        await require_consumer(request.app.state.celery, session)
    values = body.model_dump(exclude_unset=True)
    if any(values.get(field) is None for field in ("title", "is_public") if field in values):
        raise AppError(400, 1001, "标题和公开状态不能设为空")
    if body.category_id is not None and await session.get(Category, body.category_id) is None:
        raise AppError(400, 1001, "分类不存在")
    for field, value in values.items():
        setattr(document, "manual_category_id" if field == "category_id" else field, value)
    task = new_task(document, runtime.settings, "sync_payload") if document.status == "ready" else None
    if task:
        session.add(task)
    record_audit(session, request, user, "update", "document", doc_id, {"fields": list(values)})
    await bump_revision(session)
    await session.commit()
    if task:
        await dispatch(request.app.state.celery, session, str(task.id))
    return success(document_view(document))


@router.post("/{doc_id}/retry")
async def retry_document(doc_id: str, request: Request, session: Session, user: Admin, runtime: Runtime):
    document = await require_document(session, doc_id)
    if document.status not in {"failed", "ready"}:
        raise AppError(400, 1001, "文档正在处理或删除，不能重复提交")
    await require_consumer(request.app.state.celery, session)
    task = new_task(document, runtime.settings, "reindex")
    session.add(task)
    document.status, document.parse_error = "pending", None
    record_audit(session, request, user, "reindex", "document", doc_id)
    await bump_revision(session)
    await session.commit()
    await dispatch(request.app.state.celery, session, str(task.id))
    return success({"task_id": str(task.id)})


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, request: Request, session: Session, user: Admin, runtime: Runtime):
    document = await require_document(session, doc_id)
    if document.status == "deleting":
        return success({"doc_id": doc_id, "status": "deleting"})
    await require_consumer(request.app.state.celery, session)
    document.status = "deleting"
    task = new_task(document, runtime.settings, "delete")
    session.add(task)
    record_audit(session, request, user, "delete", "document", doc_id)
    await bump_revision(session)
    await session.commit()
    await dispatch(request.app.state.celery, session, str(task.id))
    return success({"doc_id": doc_id, "task_id": str(task.id), "status": "deleting"})
