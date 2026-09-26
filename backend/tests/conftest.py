from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from qdrant_client import QdrantClient, models
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import KnowledgeKey
from app.core.config import Settings
from app.core.database import make_engine
from app.core.errors import success
from app.core.security import ADMIN_AUDIENCE, PORTAL_AUDIENCE, create_access_token, hash_password
from app.main import create_app
from app.models import Account
from app.services import task_queue
from app.services.runtime_config import active_settings

TEST_PASSWORD = "Test-only-password-2026"
PASSWORD_HASH = hash_password(TEST_PASSWORD)


@pytest.fixture(scope="session")
def qdrant_collection():
    name = "knowforge_test_" + uuid4().hex
    yield name
    client = QdrantClient(url=Settings().qdrant_url)
    if client.collection_exists(name):
        client.delete_collection(name)
    client.close()


async def reset_runtime_rows(settings: Settings) -> None:
    # The app lifespan writes its startup configuration on its own committed connection.
    engine = make_engine(settings.model_copy(update={"database_url": settings.test_database_url}))
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM index_rebuilds"))
            await connection.execute(text("DELETE FROM evaluation_runs"))
            await connection.execute(text("DELETE FROM runtime_state"))
            await connection.execute(text("DELETE FROM runtime_configurations"))
    finally:
        await engine.dispose()


@pytest.fixture
async def context(tmp_path, qdrant_collection, monkeypatch):
    settings = Settings()
    assert settings.test_database_url is not None, "Configure a dedicated test database"
    assert make_url(settings.test_database_url.get_secret_value()).database == "knowforge_test"

    async def always_online(celery, session):
        return True

    # No Celery worker runs under pytest, so the consumer gate would 409 every dispatch; offline cases override this.
    monkeypatch.setattr(task_queue, "consumer_online", always_online)
    settings = settings.model_copy(
        update={
            "database_url": settings.test_database_url,
            "jwt_secret": SecretStr("test-signing-secret-" + uuid4().hex),
            "redis_prefix": "knowforge:test:" + uuid4().hex + ":",
            "qdrant_collection": qdrant_collection,
            "storage_path": tmp_path,
        }
    )
    engine = make_engine(settings)
    await reset_runtime_rows(settings)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        sessions = async_sessionmaker(
            connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        async with sessions() as session:
            admin = Account(username="admin", password_hash=PASSWORD_HASH, role="super_admin")
            editor = Account(username="editor", password_hash=PASSWORD_HASH, role="content_admin")
            customer = Account(username="customer", password_hash=PASSWORD_HASH, role="end_user")
            session.add_all([admin, editor, customer])
            await session.commit()
        app = create_app(settings)

        @app.get("/test/knowledge")
        async def protected(key: KnowledgeKey):
            return success({"key_id": key.id})

        async with app.router.lifespan_context(app):
            app.state.sessions = sessions
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                async with sessions() as session:
                    runtime = await active_settings(session, settings)
                yield {
                    "app": app,
                    "client": client,
                    "settings": settings,
                    "runtime": runtime,
                    "sessions": sessions,
                    "admin": admin,
                    "editor": editor,
                    "customer": customer,
                    "admin_headers": {
                        "Authorization": "Bearer " + create_access_token(admin.id, settings, ADMIN_AUDIENCE)
                    },
                    "editor_headers": {
                        "Authorization": "Bearer " + create_access_token(editor.id, settings, ADMIN_AUDIENCE)
                    },
                    "customer_headers": {
                        "Authorization": "Bearer "
                        + create_access_token(customer.id, settings, PORTAL_AUDIENCE)
                    },
                }
            keys = [key async for key in app.state.redis.scan_iter(match=settings.redis_prefix + "*")]
            if keys:
                await app.state.redis.unlink(*keys)
            await app.state.qdrant.delete(
                settings.qdrant_collection,
                points_selector=models.FilterSelector(filter=models.Filter()),
                wait=True,
            )
        await transaction.rollback()
    await engine.dispose()
    await reset_runtime_rows(settings)
