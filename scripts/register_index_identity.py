import asyncio

from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.services.runtime_config import index_identity, initialize_runtime


async def main() -> None:
    settings = Settings()
    engine = make_engine(settings)
    sessions = make_session_factory(engine)
    async with sessions() as session:
        config = await initialize_runtime(session, settings)
        await session.commit()
    client = AsyncQdrantClient(
        url=config.qdrant_url, api_key=config.qdrant_api_key.get_secret_value() or None
    )
    try:
        info = await client.get_collection(config.qdrant_collection)
        stored = (info.config.metadata or {}).get("knowforge_index_fingerprint")
        if stored == config.index_fingerprint:
            print("已登记，无需变更")
            return
        if stored is not None:
            raise SystemExit("该 Collection 已登记为其他索引指纹，请改用全量重建流程")
        vectors = info.config.params.vectors
        dense = vectors.get("dense") if isinstance(vectors, dict) else None
        sparse = (info.config.params.sparse_vectors or {}).get("sparse")
        if (
            dense is None
            or dense.size != config.embedding_dimension
            or dense.distance != models.Distance.COSINE
            or sparse is None
            or sparse.modifier != models.Modifier.IDF
        ):
            raise SystemExit("Collection Schema 与当前运行配置不一致，禁止登记")
        await client.update_collection(
            config.qdrant_collection, metadata={"knowforge_index_fingerprint": config.index_fingerprint}
        )
        print(
            "已登记现有索引身份：",
            config.qdrant_collection,
            config.index_fingerprint[:12],
            f"点数={info.points_count}",
            index_identity(config)["embedding_model"],
        )
    finally:
        await client.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
