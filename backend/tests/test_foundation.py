import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text

from app.core.config import ROOT, Settings
from app.core.errors import AppError
from app.core.rate_limit import enforce_limit
from app.models import Base


async def test_migrated_schema_and_trigram(context):
    async with context["app"].state.engine.connect() as connection:
        tables = await connection.run_sync(lambda conn: set(inspect(conn).get_table_names()))
        assert set(Base.metadata.tables) <= tables
        assert len(Base.metadata.tables) == 21
        assert await connection.scalar(text("SELECT similarity('redis', 'redsi')")) > 0
        indexes = await connection.run_sync(lambda conn: inspect(conn).get_indexes("chunks"))
        assert any(index["name"] == "idx_chunks_text_trgm" for index in indexes)


async def test_health_uses_real_services(context):
    response = await context["client"].get("/health")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
    assert response.headers["X-Request-ID"]


def test_config_root_and_secret_boundaries():
    assert ROOT == Path(__file__).resolve().parents[2]
    settings = Settings()
    assert str(settings.database_url) == "**********"
    assert settings.storage_path == ROOT / "data" / "storage"
    with pytest.raises(ValidationError):
        Settings(jwt_secret="short")
    with pytest.raises(ValidationError):
        Settings(model_api_base_url="http://insecure.example")


async def test_atomic_daily_quota(context):
    redis, prefix = context["app"].state.redis, context["settings"].redis_prefix
    results = await asyncio.gather(
        *[enforce_limit(redis, prefix, "parallel", 100, 3) for _ in range(10)], return_exceptions=True
    )
    assert sum(result is None for result in results) == 3
    assert sum(isinstance(result, AppError) for result in results) == 7
