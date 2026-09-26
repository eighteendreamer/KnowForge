from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document
from app.services.retrieval.rrf import Candidate


async def fuzzy_search(
    session: AsyncSession, query: str, conditions: list, limit: int = 50
) -> list[Candidate]:
    await session.execute(text("SET LOCAL pg_trgm.word_similarity_threshold = 0.2"))
    score = func.word_similarity(query, Chunk.text)
    rows = await session.execute(
        select(Chunk.chunk_id, score.label("score"))
        .join(Document, Chunk.doc_id == Document.id)
        .where(*conditions, Chunk.text.op("%>")(query))
        .order_by(score.desc(), Chunk.id)
        .limit(limit)
    )
    return [Candidate(chunk_id, value) for chunk_id, value in rows]
