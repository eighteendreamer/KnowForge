import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.routes import (
    api_keys,
    auth,
    categories,
    dashboard,
    documents,
    evaluations,
    knowledge,
    payments,
    portal_auth,
    portal_keys,
    portal_overview,
    portal_wallet,
    recharge,
    system,
    tags,
    tasks,
    users,
)
from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.core.errors import AppError, install_error_handlers, success
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS, render_metrics
from app.core.model_client import ModelGateway
from app.models import ApiLog
from app.services.catalog import seed_categories
from app.services.retrieval.bm25_encoder import warm_segmenter
from app.services.retrieval.normalize import warm_normalizer
from app.services.runtime_config import initialize_runtime
from app.services.storage.qdrant_store import QdrantStore
from app.worker.celery_app import create_celery

logger = logging.getLogger("knowforge")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(config)
        redis = Redis.from_url(config.redis_url, decode_responses=True)
        qdrant = AsyncQdrantClient(
            url=config.qdrant_url, api_key=config.qdrant_api_key.get_secret_value() or None
        )
        app.state.settings = config
        app.state.engine = engine
        app.state.sessions = make_session_factory(engine)
        app.state.redis = redis
        app.state.qdrant = qdrant
        app.state.models = ModelGateway(config, redis)
        app.state.celery = create_celery(config)
        # jieba builds its dictionary lazily; paying ~0.45s inside the first search would stall every route.
        await asyncio.to_thread(warm_segmenter)
        await asyncio.to_thread(warm_normalizer)
        try:
            for attempt in range(3):
                try:
                    async with engine.connect() as connection:
                        await connection.execute(text("SELECT 1"))
                    await redis.ping()
                    await qdrant.get_collections()
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.25 * 2**attempt)
            async with app.state.sessions() as session:
                await seed_categories(session)
                await session.commit()
            async with app.state.sessions() as session:
                runtime = await initialize_runtime(session, config)
                await session.commit()
            await QdrantStore(qdrant, runtime).ensure_collection()
            yield
        finally:
            await app.state.models.close()
            app.state.celery.close()
            await qdrant.close()
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="KnowForge 技术知识库", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(api_keys.router)
    app.include_router(documents.router)
    app.include_router(tasks.router)
    app.include_router(knowledge.router)
    app.include_router(knowledge.admin_router)
    app.include_router(portal_auth.router)
    app.include_router(portal_keys.router)
    app.include_router(portal_overview.router)
    app.include_router(portal_wallet.router)
    app.include_router(payments.router)
    app.include_router(payments.notify_router)
    app.include_router(recharge.router)
    app.include_router(tags.router)
    app.include_router(tags.document_router)
    app.include_router(categories.router)
    app.include_router(dashboard.router)
    app.include_router(evaluations.router)
    app.include_router(system.router)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = uuid4()
        started = time.perf_counter()
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error("request_failed request_id=%s error_type=%s", request_id, type(exc).__name__)
            response = JSONResponse(
                {"code": 5001, "message": "服务器内部错误", "request_id": str(request_id)}, status_code=500
            )
        response.headers["X-Request-ID"] = str(request_id)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        if getattr(request.state, "api_key_id", None):
            try:
                async with request.app.state.sessions() as session:
                    session.add(
                        ApiLog(
                            request_id=request_id,
                            api_key_id=request.state.api_key_id,
                            endpoint=request.url.path[:100],
                            method=request.method,
                            query=getattr(request.state, "search_query", None),
                            search_type=getattr(request.state, "search_type", None),
                            top_k=getattr(request.state, "top_k", None),
                            result_count=getattr(request.state, "result_count", None),
                            latency_ms=int((time.perf_counter() - started) * 1000),
                            status_code=response.status_code,
                        )
                    )
                    await session.commit()
            except Exception as exc:
                logger.error("api_log_failed request_id=%s error_type=%s", request_id, type(exc).__name__)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        if route != "/metrics":
            method = (
                request.method
                if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                else "OTHER"
            )
            HTTP_REQUESTS.labels(method, route, str(response.status_code)).inc()
            HTTP_DURATION.labels(method, route).observe(time.perf_counter() - started)
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        token = config.metrics_token.get_secret_value()
        if not token:
            raise AppError(404, 1001, "指标端点未启用")
        authorization = request.headers.get("Authorization", "")
        if not secrets.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
            raise AppError(401, 2001, "指标凭据无效")
        return Response(await render_metrics(request.app), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @app.get("/health", tags=["健康检查"])
    async def health(request: Request):
        async with request.app.state.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await request.app.state.redis.ping()
        await request.app.state.qdrant.get_collections()
        return success({"status": "ok", "models_configured": config.models_configured})

    return app
