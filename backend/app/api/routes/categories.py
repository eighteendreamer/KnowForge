from fastapi import APIRouter, Request
from sqlalchemy import func, or_, select

from app.api.deps import Admin, Session
from app.core.errors import AppError, success
from app.models import Category, Chunk, Document
from app.schemas.catalog import CategoryInput, CategoryMove, CategoryPatch, MoveDocuments
from app.services.audit import record_audit
from app.services.catalog_sync import enqueue_catalog_sync, lock_catalog
from app.services.runtime_config import active_settings
from app.services.task_queue import dispatch, require_consumer

router = APIRouter(prefix="/v1/admin/categories", tags=["分类管理"])


def category_view(category: Category, count: int = 0) -> dict:
    return {
        "id": category.id,
        "name": category.name,
        "path": category.path,
        "parent_id": category.parent_id,
        "sort_order": category.sort_order,
        "document_count": count,
    }


async def dispatch_changes(session, request: Request, document_ids: set[int]) -> None:
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
async def list_categories(session: Session, user: Admin):
    categories = list(await session.scalars(select(Category).order_by(Category.sort_order, Category.id)))
    counts = {
        category_id: count
        for category_id, count in await session.execute(
            select(func.coalesce(Document.manual_category_id, Document.auto_category_id), func.count())
            .where(Document.status != "deleting")
            .group_by(func.coalesce(Document.manual_category_id, Document.auto_category_id))
        )
    }
    nodes = {
        category.id: {**category_view(category, counts.get(category.id, 0)), "children": []}
        for category in categories
    }
    tree = []
    for category in categories:
        if category.parent_id in nodes:
            nodes[category.parent_id]["children"].append(nodes[category.id])
        else:
            tree.append(nodes[category.id])
    return success(
        {
            "items": [category_view(category, counts.get(category.id, 0)) for category in categories],
            "tree": tree,
        }
    )


@router.post("")
async def create_category(body: CategoryInput, request: Request, session: Session, user: Admin):
    await lock_catalog(session)
    parent = await session.get(Category, body.parent_id) if body.parent_id else None
    if body.parent_id and parent is None:
        raise AppError(404, 1001, "父分类不存在")
    path = f"{parent.path}/{body.name}" if parent else body.name
    if len(path) > 500:
        raise AppError(400, 1001, "分类路径超过长度限制")
    category = Category(name=body.name, parent_id=body.parent_id, path=path, sort_order=body.sort_order)
    session.add(category)
    await session.flush()
    record_audit(session, request, user, "create", "category", category.id)
    await dispatch_changes(session, request, set())
    return success(category_view(category))


@router.post("/move-documents")
async def move_documents(body: MoveDocuments, request: Request, session: Session, user: Admin):
    await lock_catalog(session)
    if body.category_id is not None and await session.get(Category, body.category_id) is None:
        raise AppError(404, 1001, "目标分类不存在")
    documents = list(
        await session.scalars(
            select(Document).where(Document.doc_id.in_(set(body.doc_ids)), Document.status != "deleting")
        )
    )
    if len(documents) != len(set(body.doc_ids)):
        raise AppError(404, 1001, "部分文档不存在或正在删除")
    for document in documents:
        document.manual_category_id = body.category_id
        record_audit(
            session,
            request,
            user,
            "move_category",
            "document",
            document.doc_id,
            {"category_id": body.category_id},
        )
    await dispatch_changes(session, request, {document.id for document in documents})
    return success({"updated": len(documents)})


async def apply_category_change(category, categories, changes, request, session, user):
    by_id = {item.id: item for item in categories}
    parent_id = changes.get("parent_id", category.parent_id)
    if parent_id is not None and parent_id not in by_id:
        raise AppError(404, 1001, "父分类不存在")
    ancestor_id = parent_id
    while ancestor_id is not None:
        if ancestor_id == category.id:
            raise AppError(400, 1001, "不能将分类移动到自身或其子分类下")
        ancestor_id = by_id[ancestor_id].parent_id
    for field, value in changes.items():
        setattr(category, field, value)
    affected = set()
    queue = [category]
    while queue:
        current = queue.pop(0)
        parent_path = by_id[current.parent_id].path if current.parent_id else ""
        path = f"{parent_path}/{current.name}" if parent_path else current.name
        if len(path) > 500:
            raise AppError(400, 1001, "分类路径超过长度限制")
        if current.path != path:
            affected.add(current.id)
            current.path = path
        queue.extend(child for child in categories if child.parent_id == current.id)
    await session.flush()
    document_ids = set()
    if affected:
        document_ids.update(
            await session.scalars(
                select(Document.id).where(
                    or_(Document.manual_category_id.in_(affected), Document.auto_category_id.in_(affected))
                )
            )
        )
        document_ids.update(
            await session.scalars(select(Chunk.doc_id).where(Chunk.category_id.in_(affected)))
        )
    record_audit(session, request, user, "update", "category", category.id, {"fields": list(changes)})
    await dispatch_changes(session, request, document_ids)
    return success(category_view(category))


@router.patch("/{category_id}")
async def patch_category(
    category_id: int, body: CategoryPatch, request: Request, session: Session, user: Admin
):
    await lock_catalog(session)
    categories = list(await session.scalars(select(Category)))
    category = next((item for item in categories if item.id == category_id), None)
    if category is None:
        raise AppError(404, 1001, "分类不存在")
    changes = body.model_dump(exclude_unset=True)
    if any(value is None for key, value in changes.items() if key != "parent_id"):
        raise AppError(400, 1001, "分类名称和顺序不能设为空")
    return await apply_category_change(category, categories, changes, request, session, user)


@router.post("/{category_id}/move")
async def move_category(
    category_id: int, body: CategoryMove, request: Request, session: Session, user: Admin
):
    await lock_catalog(session)
    categories = list(await session.scalars(select(Category).order_by(Category.sort_order, Category.id)))
    by_id = {item.id: item for item in categories}
    category, target = by_id.get(category_id), by_id.get(body.target_id)
    if category is None or target is None:
        raise AppError(404, 1001, "分类不存在")
    if category.id == target.id:
        raise AppError(400, 1001, "不能将分类拖拽到自身")
    parent_id = target.id if body.position == "inside" else target.parent_id
    siblings = [item for item in categories if item.parent_id == parent_id and item.id != category.id]
    position = (
        len(siblings) if body.position == "inside" else siblings.index(target) + (body.position == "after")
    )
    siblings.insert(position, category)
    for order, sibling in enumerate(siblings):
        sibling.sort_order = order
    if category.parent_id != parent_id:
        old_siblings = [
            item for item in categories if item.parent_id == category.parent_id and item.id != category.id
        ]
        for order, sibling in enumerate(old_siblings):
            sibling.sort_order = order
    return await apply_category_change(
        category, categories, {"parent_id": parent_id, "sort_order": position}, request, session, user
    )


@router.delete("/{category_id}")
async def delete_category(category_id: int, request: Request, session: Session, user: Admin):
    await lock_catalog(session)
    category = await session.get(Category, category_id)
    if category is None:
        raise AppError(404, 1001, "分类不存在")
    child = await session.scalar(select(Category.id).where(Category.parent_id == category_id).limit(1))
    document = await session.scalar(
        select(Document.id)
        .where(or_(Document.auto_category_id == category_id, Document.manual_category_id == category_id))
        .limit(1)
    )
    chunk = await session.scalar(select(Chunk.id).where(Chunk.category_id == category_id).limit(1))
    if child or document or chunk:
        raise AppError(400, 1001, "请先移动子分类和关联文档，再删除分类")
    await session.delete(category)
    record_audit(session, request, user, "delete", "category", category_id)
    await dispatch_changes(session, request, set())
    return success({"deleted": category_id})
