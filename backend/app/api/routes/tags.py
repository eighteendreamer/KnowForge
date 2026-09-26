from fastapi import APIRouter, Query, Request
from sqlalchemy import case, delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import Admin, Session
from app.core.errors import AppError, success
from app.models import Chunk, ChunkTag, Document, DocumentTag, Tag
from app.schemas.catalog import TagBatch, TagInput, TagMerge, TagPatch
from app.schemas.documents import DocumentTagsInput
from app.services.audit import record_audit
from app.services.catalog_sync import enqueue_catalog_sync, lock_catalog
from app.services.runtime_config import active_settings
from app.services.task_queue import dispatch, require_consumer

router = APIRouter(prefix="/v1/admin/tags", tags=["标签管理"])
document_router = APIRouter(prefix="/v1/admin/documents", tags=["文档标签"])


def tag_view(tag: Tag, count: int = 0) -> dict:
    return {
        "id": tag.id,
        "name": tag.name,
        "color": tag.color,
        "auto_generated": tag.auto_generated,
        "confidence": tag.confidence,
        "review_status": tag.review_status,
        "document_count": count,
    }


async def associated_documents(session, ids: list[int]) -> set[int]:
    direct = set(await session.scalars(select(DocumentTag.doc_id).where(DocumentTag.tag_id.in_(ids))))
    chunks = set(
        await session.scalars(
            select(Chunk.doc_id).join(ChunkTag, ChunkTag.chunk_id == Chunk.id).where(ChunkTag.tag_id.in_(ids))
        )
    )
    return direct | chunks


async def commit_and_dispatch(session, request: Request, document_ids: set[int]) -> None:
    if not document_ids:
        await session.commit()
        return
    # The gate runs before commit, so a refusal discards the uncommitted catalogue edits instead of stranding them.
    await require_consumer(request.app.state.celery, session)
    settings = await active_settings(session, request.app.state.settings)
    tasks = await enqueue_catalog_sync(session, document_ids, settings)
    await session.commit()
    for task in tasks:
        await dispatch(request.app.state.celery, session, str(task.id))


@router.get("")
async def list_tags(
    session: Session,
    user: Admin,
    q: str = Query("", max_length=100),
    review_status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conditions = []
    if q:
        conditions.append(Tag.name.icontains(q, autoescape=True))
    if review_status:
        conditions.append(Tag.review_status == review_status)
    counts = (
        select(DocumentTag.tag_id, func.count(func.distinct(DocumentTag.doc_id)).label("count"))
        .group_by(DocumentTag.tag_id)
        .subquery()
    )
    rows = await session.execute(
        select(Tag, func.coalesce(counts.c.count, 0))
        .outerjoin(counts, counts.c.tag_id == Tag.id)
        .where(*conditions)
        .order_by(Tag.id.desc())
        .offset(offset)
        .limit(limit)
    )
    total = await session.scalar(select(func.count()).select_from(Tag).where(*conditions))
    return success({"items": [tag_view(tag, count) for tag, count in rows], "total": total})


@router.post("")
async def create_tag(body: TagInput, request: Request, session: Session, user: Admin):
    await lock_catalog(session)
    tag = Tag(
        name=body.name,
        normalized_name=body.name.casefold(),
        color=body.color,
        review_status="approved",
        confidence=1,
        auto_generated=False,
    )
    session.add(tag)
    await session.flush()
    record_audit(session, request, user, "create", "tag", tag.id)
    await commit_and_dispatch(session, request, set())
    return success(tag_view(tag))


@router.post("/merge")
async def merge_tags(body: TagMerge, request: Request, session: Session, user: Admin):
    await lock_catalog(session)
    source_ids = sorted(set(body.source_ids) - {body.target_id})
    if not source_ids:
        raise AppError(400, 1001, "请选择不同的源标签与目标标签")
    target = await session.get(Tag, body.target_id)
    sources = list(await session.scalars(select(Tag).where(Tag.id.in_(source_ids))))
    if target is None or len(sources) != len(source_ids):
        raise AppError(404, 1001, "标签不存在")
    document_ids = await associated_documents(session, source_ids + [body.target_id])
    models: list[type[DocumentTag] | type[ChunkTag]] = [DocumentTag, ChunkTag]
    for model in models:
        owner = DocumentTag.doc_id if model is DocumentTag else ChunkTag.chunk_id
        for source_id in source_ids:
            statement = insert(model).from_select(
                [owner.key, "tag_id", "source", "confidence", "created_at"],
                select(
                    owner, literal(body.target_id), model.source, model.confidence, model.created_at
                ).where(model.tag_id == source_id),
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[owner, model.tag_id],
                    set_={
                        "source": case(
                            (model.source == "manual", "manual"),
                            (statement.excluded.source == "manual", "manual"),
                            else_=model.source,
                        ),
                        "confidence": func.greatest(model.confidence, statement.excluded.confidence),
                    },
                )
            )
    await session.execute(delete(Tag).where(Tag.id.in_(source_ids)))
    record_audit(session, request, user, "merge", "tag", target.id, {"source_ids": source_ids})
    await commit_and_dispatch(session, request, document_ids)
    return success(tag_view(target))


@router.post("/batch-review")
async def review_tags(body: TagBatch, request: Request, session: Session, user: Admin):
    if body.review_status is None:
        raise AppError(400, 1001, "缺少审核结果")
    await lock_catalog(session)
    tags = list(await session.scalars(select(Tag).where(Tag.id.in_(set(body.ids)))))
    if len(tags) != len(set(body.ids)):
        raise AppError(404, 1001, "部分标签不存在")
    document_ids = await associated_documents(session, body.ids)
    for tag in tags:
        tag.review_status = body.review_status
        record_audit(session, request, user, "review", "tag", tag.id, {"review_status": body.review_status})
    await commit_and_dispatch(session, request, document_ids)
    return success({"updated": len(tags)})


@router.post("/batch-delete")
async def delete_tags(body: TagBatch, request: Request, session: Session, user: Admin):
    await lock_catalog(session)
    ids = set(body.ids)
    found = set(await session.scalars(select(Tag.id).where(Tag.id.in_(ids))))
    if found != ids:
        raise AppError(404, 1001, "部分标签不存在")
    document_ids = await associated_documents(session, body.ids)
    await session.execute(delete(Tag).where(Tag.id.in_(ids)))
    record_audit(session, request, user, "batch_delete", "tag", "batch", {"ids": sorted(ids)})
    await commit_and_dispatch(session, request, document_ids)
    return success({"deleted": len(ids)})


@router.patch("/{tag_id}")
async def patch_tag(tag_id: int, body: TagPatch, request: Request, session: Session, user: Admin):
    await lock_catalog(session)
    tag = await session.get(Tag, tag_id)
    if tag is None:
        raise AppError(404, 1001, "标签不存在")
    document_ids = await associated_documents(session, [tag_id])
    changes = body.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise AppError(400, 1001, "标签字段不能设为空")
    for field, value in changes.items():
        setattr(tag, field, value)
    if body.name:
        tag.normalized_name = body.name.casefold()
    record_audit(session, request, user, "update", "tag", tag.id, {"fields": list(changes)})
    await commit_and_dispatch(session, request, document_ids)
    return success(tag_view(tag))


@router.get("/{tag_id}/documents")
async def tag_documents(tag_id: int, session: Session, user: Admin):
    rows = await session.scalars(
        select(Document)
        .join(DocumentTag, DocumentTag.doc_id == Document.id)
        .where(DocumentTag.tag_id == tag_id)
        .order_by(Document.id.desc())
        .limit(50)
    )
    return success(
        {"items": [{"doc_id": row.doc_id, "title": row.title, "status": row.status} for row in rows]}
    )


@document_router.get("/{doc_id}/tags")
async def document_tags(doc_id: str, session: Session, user: Admin):
    document = await session.scalar(select(Document).where(Document.doc_id == doc_id))
    if document is None:
        raise AppError(404, 1001, "文档不存在")
    rows = await session.scalars(
        select(Tag)
        .join(DocumentTag, DocumentTag.tag_id == Tag.id)
        .where(DocumentTag.doc_id == document.id)
        .order_by(Tag.name)
    )
    return success({"items": [tag_view(tag) for tag in rows]})


@document_router.put("/{doc_id}/tags")
async def replace_document_tags(
    doc_id: str, body: DocumentTagsInput, request: Request, session: Session, user: Admin
):
    await lock_catalog(session)
    document = await session.scalar(select(Document).where(Document.doc_id == doc_id))
    if document is None or document.status == "deleting":
        raise AppError(404, 1001, "文档不存在或正在删除")
    approved = set(
        await session.scalars(select(Tag.id).where(Tag.id.in_(body.tag_ids), Tag.review_status == "approved"))
    )
    if approved != set(body.tag_ids):
        raise AppError(400, 1001, "只能关联已审核的有效标签")
    chunk_ids = list(await session.scalars(select(Chunk.id).where(Chunk.doc_id == document.id)))
    await session.execute(delete(DocumentTag).where(DocumentTag.doc_id == document.id))
    await session.execute(delete(ChunkTag).where(ChunkTag.chunk_id.in_(chunk_ids)))
    for tag_id in sorted(approved):
        session.add(DocumentTag(doc_id=document.id, tag_id=tag_id, source="manual", confidence=1))
        for chunk_id in chunk_ids:
            session.add(ChunkTag(chunk_id=chunk_id, tag_id=tag_id, source="manual", confidence=1))
    record_audit(session, request, user, "set_tags", "document", doc_id, {"tag_ids": sorted(approved)})
    await commit_and_dispatch(session, request, {document.id})
    return success({"doc_id": doc_id, "tag_ids": sorted(approved)})
