import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.schemas.search import SearchInput
from app.services.retrieval.search_service import search
from app.services.storage.qdrant_store import QdrantStore


class SlowGateway:
    """Blocks inside the remote model call so the test can inspect the pool mid-flight."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def embed(self, _texts, _priority="batch"):
        self.entered.set()
        await self.release.wait()
        return [[0.1] * 4096]


@pytest.fixture
async def tiny_pool(context):
    """A pool that collapses the moment one connection stays checked out."""
    engine = create_async_engine(
        context["settings"].database_url.get_secret_value(),
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    try:
        yield engine, async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_search_leaves_the_whole_pool_free_while_the_model_is_running(context, tiny_pool):
    engine, sessions = tiny_pool
    gateway = SlowGateway()
    async with sessions() as session:
        task = asyncio.create_task(
            search(
                session,
                SearchInput(
                    query="Redis 缓存穿透",
                    search_type="semantic",
                    options={"query_rewrite": False, "rerank": False, "highlight": False},
                ),
                context["runtime"],
                gateway,
                QdrantStore(context["app"].state.qdrant, context["runtime"]),
                context["app"].state.redis,
                public_only=False,
            )
        )
        try:
            await asyncio.wait_for(gateway.entered.wait(), timeout=5)
            async with engine.connect() as connection:
                assert (await connection.execute(text("SELECT 1"))).scalar() == 1
            gateway.release.set()
            data = await asyncio.wait_for(task, timeout=10)
        finally:
            task.cancel()
            await session.rollback()
    assert data["results"] == []


async def test_an_open_transaction_does_starve_the_same_pool(tiny_pool):
    """Guard for the guard: the pool above really refuses a second checkout while a session works."""
    engine, sessions = tiny_pool
    async with sessions() as session:
        await session.execute(text("SELECT 1"))
        with pytest.raises(PoolTimeout):
            async with engine.connect():
                pass
        await session.rollback()
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar() == 1
