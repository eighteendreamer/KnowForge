import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.core.errors import AppError
from app.core.model_client import ModelGateway
from app.schemas.search import SearchInput
from app.services.retrieval.search_service import search
from app.services.runtime_config import active_settings
from app.services.storage.qdrant_store import QdrantStore

ROOT = Path(__file__).resolve().parents[1]


class TimedGateway(ModelGateway):
    def __init__(self, settings, redis):
        super().__init__(settings, redis)
        self.measurements = []
        self.operation_name = ""

    @asynccontextmanager
    async def slot(self, priority):
        started = time.perf_counter()
        async with super().slot(priority):
            self.measurements.append(
                {
                    "phase": self.operation_name + ":quota_wait",
                    "ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
            yield

    async def request(self, operation, priority="online", **kwargs):
        async def timed_operation():
            started = time.perf_counter()
            outcome = "ok"
            try:
                return await operation()
            except BaseException as exc:
                outcome = type(exc).__name__
                raise
            finally:
                self.measurements.append(
                    {
                        "phase": self.operation_name + ":remote",
                        "ms": round((time.perf_counter() - started) * 1000, 2),
                        "outcome": outcome,
                    }
                )

        return await super().request(timed_operation, priority, **kwargs)

    async def embed(self, texts, priority="batch"):
        self.operation_name = "embedding"
        return await super().embed(texts, priority)

    async def json_chat(self, system, content, priority="batch"):
        self.operation_name = "rewrite"
        return await super().json_chat(system, content, priority)

    async def rerank(self, query, documents):
        self.operation_name = "rerank"
        self.measurements.append(
            {"phase": "rerank:input", "documents": len(documents), "characters": sum(map(len, documents))}
        )
        return await super().rerank(query, documents)


async def main():
    defaults = Settings()
    engine = make_engine(defaults)
    sessions = make_session_factory(engine)
    redis = Redis.from_url(defaults.redis_url, decode_responses=True)
    qdrant = AsyncQdrantClient(
        url=defaults.qdrant_url, api_key=defaults.qdrant_api_key.get_secret_value() or None
    )
    # 检索要跑在生产同一份运行配置上：代码默认值没有索引身份，QdrantStore 会直接拒绝绑定。
    async with sessions() as session:
        settings = await active_settings(session, defaults)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "scope": "single-user diagnostic; not load-test or relevance benchmark",
        "index": {
            "collection": settings.qdrant_collection,
            "configuration_id": str(settings.configuration_id),
            "index_fingerprint": settings.index_fingerprint,
        },
        "runs": [],
    }
    gateway = TimedGateway(settings, redis)
    try:
        for mode in ["semantic", "keyword", "fuzzy", "hybrid"]:
            for repeat in range(2):
                gateway.measurements = []
                body = SearchInput(query="Redis 缓存穿透如何解决", search_type=mode, top_k=5)
                started = time.perf_counter()
                row = {"mode": mode, "repeat": repeat}
                try:
                    async with sessions() as session:
                        data = await search(
                            session,
                            body,
                            settings,
                            gateway,
                            QdrantStore(qdrant, settings),
                            redis,
                            public_only=False,
                        )
                    row.update({"status": "ok", "took_ms": data["took_ms"], "results": len(data["results"])})
                except AppError as exc:
                    row.update(
                        {
                            "status": "failed",
                            "error_code": exc.code,
                            "took_ms": round((time.perf_counter() - started) * 1000),
                        }
                    )
                row["phases"] = gateway.measurements.copy()
                report["runs"].append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                if row["status"] == "failed":
                    break
        if any(row["status"] == "failed" for row in report["runs"]):
            raise SystemExit(1)
    finally:
        path = ROOT / "data/acceptance/search-profile.json"
        await asyncio.to_thread(
            path.write_text, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        await gateway.close()
        await qdrant.close()
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
