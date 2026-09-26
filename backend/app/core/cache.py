import hashlib
import json
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting


async def bump_revision(session: AsyncSession) -> None:
    statement = insert(SystemSetting).values(key="knowledge_revision", value={"revision": uuid4().hex})
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[SystemSetting.key],
            set_={"value": statement.excluded.value, "updated_at": func.now()},
        )
    )


async def revision(session: AsyncSession) -> str:
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == "knowledge_revision"))
    return str(row.value["revision"]) if row else "0"


def search_cache_key(
    prefix: str, payload: dict, revision_id: str, model: str, collection: str, scope: str
) -> str:
    value = json.dumps([payload, revision_id, model, collection, scope], sort_keys=True, ensure_ascii=False)
    return prefix + "search:" + hashlib.sha256(value.encode()).hexdigest()


async def cached_search(redis: Redis, key: str) -> dict | None:
    content = await redis.get(key)
    return json.loads(content) if content is not None else None
