from qdrant_client import models
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Chunk, ChunkTag, Document, Tag
from app.schemas.search import SearchFilters


async def category_scope(session: AsyncSession, path: str) -> list[Category]:
    roots = select(Category.id).where(Category.path == path).cte("category_subtree", recursive=True)
    roots = roots.union_all(select(Category.id).join(roots, Category.parent_id == roots.c.id))
    return list(await session.scalars(select(Category).where(Category.id.in_(select(roots.c.id)))))


async def database_filters(session: AsyncSession, filters: SearchFilters, public_only: bool) -> list:
    conditions = [Document.status == "ready"]
    if public_only:
        conditions.append(Document.is_public.is_(True))
    if filters.category:
        categories = await category_scope(session, filters.category)
        conditions.append(
            func.coalesce(Document.manual_category_id, Chunk.category_id, Document.auto_category_id).in_(
                [category.id for category in categories]
            )
        )
    if filters.difficulty:
        conditions.append(Chunk.difficulty == filters.difficulty)
    for tag in filters.tags:
        conditions.append(
            exists(
                select(ChunkTag.chunk_id)
                .join(Tag, ChunkTag.tag_id == Tag.id)
                .where(ChunkTag.chunk_id == Chunk.id, Tag.name == tag, Tag.review_status == "approved")
            )
        )
    if filters.exclude_tags:
        conditions.append(
            ~exists(
                select(ChunkTag.chunk_id)
                .join(Tag, ChunkTag.tag_id == Tag.id)
                .where(
                    ChunkTag.chunk_id == Chunk.id,
                    Tag.name.in_(filters.exclude_tags),
                    Tag.review_status == "approved",
                )
            )
        )
    return conditions


async def vector_filters(session: AsyncSession, filters: SearchFilters, public_only: bool) -> models.Filter:
    must: list[models.Condition] = []
    must_not: list[models.Condition] = []
    if public_only:
        must.append(models.FieldCondition(key="is_public", match=models.MatchValue(value=True)))
    if filters.category:
        categories = await category_scope(session, filters.category)
        must.append(
            models.FieldCondition(
                key="category", match=models.MatchAny(any=[category.path for category in categories])
            )
        )
    if filters.difficulty:
        must.append(
            models.FieldCondition(key="difficulty", match=models.MatchValue(value=filters.difficulty))
        )
    for tag in filters.tags:
        must.append(models.FieldCondition(key="tags", match=models.MatchValue(value=tag)))
    if filters.exclude_tags:
        must_not.append(models.FieldCondition(key="tags", match=models.MatchAny(any=filters.exclude_tags)))
    return models.Filter(must=must, must_not=must_not)
