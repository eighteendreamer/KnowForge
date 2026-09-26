from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import select

from app.api.deps import Admin, Session
from app.core.errors import AppError, success
from app.core.privacy import redact_query
from app.models import Chunk, RelevanceJudgment
from app.schemas.identity import StrictModel
from app.services.audit import record_audit

router = APIRouter(prefix="/v1/admin/evaluations", tags=["检索质量标注"])


class EvaluationInput(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    chunk_id: str = Field(min_length=1, max_length=100)
    judgment: Literal[0, 1, 2]
    notes: str | None = Field(default=None, max_length=2000)


@router.post("")
async def record_evaluation(body: EvaluationInput, request: Request, session: Session, user: Admin):
    chunk = await session.scalar(select(Chunk).where(Chunk.chunk_id == body.chunk_id))
    if chunk is None:
        raise AppError(404, 1001, "分块不存在")
    query = redact_query(body.query)
    existing = await session.scalar(
        select(RelevanceJudgment).where(
            RelevanceJudgment.query == query,
            RelevanceJudgment.chunk_id == chunk.id,
            RelevanceJudgment.judge_id == user.id,
            RelevanceJudgment.judge_type == "human",
        )
    )
    if existing is None:
        existing = RelevanceJudgment(
            query=query,
            chunk_id=chunk.id,
            judge_id=user.id,
            judge_type="human",
            judgment=body.judgment,
            notes=body.notes,
        )
        session.add(existing)
    else:
        existing.judgment, existing.notes = body.judgment, body.notes
    await session.flush()
    record_audit(session, request, user, "judge", "relevance", existing.id, {"judgment": body.judgment})
    await session.commit()
    return success({"id": existing.id, "judgment": existing.judgment})


@router.get("")
async def list_evaluations(
    session: Session, user: Admin, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)
):
    rows = await session.execute(
        select(RelevanceJudgment, Chunk.chunk_id)
        .join(Chunk, Chunk.id == RelevanceJudgment.chunk_id)
        .order_by(RelevanceJudgment.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return success(
        {
            "items": [
                {
                    "id": row.id,
                    "query": row.query,
                    "chunk_id": chunk_id,
                    "judgment": row.judgment,
                    "judge_type": row.judge_type,
                    "notes": row.notes,
                    "created_at": row.created_at,
                }
                for row, chunk_id in rows
            ]
        }
    )


@router.get("/export")
async def export_evaluations(session: Session, user: Admin):
    rows = await session.execute(
        select(RelevanceJudgment, Chunk.chunk_id)
        .join(Chunk, Chunk.id == RelevanceJudgment.chunk_id)
        .order_by(RelevanceJudgment.id)
    )
    return success(
        {
            "items": [
                {
                    "query": row.query,
                    "chunk_id": chunk_id,
                    "judgment": row.judgment,
                    "judge_type": row.judge_type,
                    "notes": row.notes,
                }
                for row, chunk_id in rows
            ]
        }
    )
