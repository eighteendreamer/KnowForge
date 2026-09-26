from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Chunk, ChunkTag, Document, Tag
from app.schemas.documents import ChunkData
from app.services.storage.qdrant_store import QdrantStore


async def chunk_data(session: AsyncSession, document: Document) -> list[ChunkData]:
    rows = list(
        await session.scalars(select(Chunk).where(Chunk.doc_id == document.id).order_by(Chunk.chunk_index))
    )
    categories = {row.id: row.path for row in await session.scalars(select(Category))}
    associations = await session.execute(
        select(ChunkTag.chunk_id, Tag.name)
        .join(Tag, ChunkTag.tag_id == Tag.id)
        .join(Chunk, ChunkTag.chunk_id == Chunk.id)
        .where(Chunk.doc_id == document.id, Tag.review_status == "approved")
    )
    tags: dict[int, list[str]] = {}
    for chunk_id, name in associations:
        tags.setdefault(chunk_id, []).append(name)
    result = []
    for row in rows:
        category_id = document.manual_category_id or row.category_id or document.auto_category_id
        metadata = {
            **row.meta,
            "source": document.original_filename,
            "tags": sorted(tags.get(row.id, [])),
            "category": categories.get(category_id, "") if category_id is not None else "",
            "difficulty": row.difficulty or "",
        }
        result.append(
            ChunkData(
                chunk_id=row.chunk_id,
                doc_id=document.doc_id,
                chunk_index=row.chunk_index,
                section_path=row.section_path,
                text=row.text,
                text_with_context=row.text_with_context,
                page_start=row.page_start,
                page_end=row.page_end,
                char_count=row.char_count,
                token_count=row.token_count,
                element_types=row.element_types,
                metadata=metadata,
            )
        )
    return result


async def sync_document_payload(session: AsyncSession, document: Document, store: QdrantStore) -> None:
    for chunk in await chunk_data(session, document):
        payload = {
            "tags": chunk.metadata["tags"],
            "category": chunk.metadata["category"],
            "difficulty": chunk.metadata["difficulty"],
            "is_public": document.is_public,
            "source": document.original_filename,
        }
        await store.client.set_payload(
            store.collection,
            payload=payload,
            points=[str(UUID(hex=chunk.chunk_id.removeprefix("chunk_")))],
            wait=True,
        )
