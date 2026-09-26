from uuid import UUID

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import Settings
from app.schemas.documents import ChunkData
from app.services.retrieval.bm25_encoder import bm25_vector

PAYLOAD_INDEXES = {
    "doc_id": models.PayloadSchemaType.KEYWORD,
    "chunk_id": models.PayloadSchemaType.KEYWORD,
    "tags": models.PayloadSchemaType.KEYWORD,
    "category": models.PayloadSchemaType.KEYWORD,
    "difficulty": models.PayloadSchemaType.KEYWORD,
    "page_start": models.PayloadSchemaType.INTEGER,
    "page_end": models.PayloadSchemaType.INTEGER,
    "is_public": models.PayloadSchemaType.BOOL,
}
FINGERPRINT_KEY = "knowforge_index_fingerprint"
WRITE_BATCH_POINTS = 64


class QdrantStore:
    """Every instance is bound to one runtime configuration, so its collection identity is checked."""

    def __init__(self, client: AsyncQdrantClient, settings: Settings):
        self.client = client
        self.collection = settings.qdrant_collection
        self.dimension = settings.embedding_dimension
        fingerprint = settings.index_fingerprint
        if fingerprint is None:
            raise ValueError("Qdrant 存储必须绑定带索引指纹的运行配置")
        self.fingerprint = fingerprint

    async def ensure_collection(self) -> None:
        if not await self.client.collection_exists(self.collection):
            try:
                await self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config={
                        "dense": models.VectorParams(size=self.dimension, distance=models.Distance.COSINE)
                    },
                    sparse_vectors_config={"sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)},
                    shard_number=2,
                    replication_factor=1,
                    hnsw_config=models.HnswConfigDiff(m=16, ef_construct=200),
                    metadata={FINGERPRINT_KEY: self.fingerprint},
                )
            except UnexpectedResponse:
                if not await self.client.collection_exists(self.collection):
                    raise
        info = await self.client.get_collection(self.collection)
        vectors = info.config.params.vectors
        dense = vectors.get("dense") if isinstance(vectors, dict) else None
        sparse = (info.config.params.sparse_vectors or {}).get("sparse")
        if dense is None or dense.size != self.dimension or dense.distance != models.Distance.COSINE:
            raise ValueError("Qdrant Dense Schema 与当前模型配置不一致，必须重建索引")
        if sparse is None or sparse.modifier != models.Modifier.IDF:
            raise ValueError("Qdrant Sparse Schema 必须启用 IDF")
        stored = (info.config.metadata or {}).get(FINGERPRINT_KEY)
        if stored != self.fingerprint:
            if stored is not None or (info.points_count or 0) > 0:
                raise ValueError("Collection 已绑定其他模型或分块指纹，禁止混用向量")
            await self.client.update_collection(self.collection, metadata={FINGERPRINT_KEY: self.fingerprint})
        for field, schema in PAYLOAD_INDEXES.items():
            if field not in info.payload_schema:
                await self.client.create_payload_index(
                    self.collection, field_name=field, field_schema=schema, wait=True
                )

    async def point_count(self) -> int:
        return (await self.client.count(self.collection, exact=True)).count

    async def upsert(self, chunks: list[ChunkData], vectors: list[list[float]], is_public: bool) -> None:
        if len(chunks) != len(vectors) or any(len(vector) != self.dimension for vector in vectors):
            raise ValueError("Embedding 数量或维度不匹配")
        points = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            payload = {**chunk.model_dump(exclude={"metadata"}), **chunk.metadata, "is_public": is_public}
            payload["page"] = chunk.page_start
            payload = {key: value for key, value in payload.items() if value is not None}
            points.append(
                models.PointStruct(
                    id=str(UUID(hex=chunk.chunk_id.removeprefix("chunk_"))),
                    vector={"dense": vector, "sparse": bm25_vector(chunk.text_with_context)},
                    payload=payload,
                )
            )
        if points:
            for start in range(0, len(points), WRITE_BATCH_POINTS):
                await self.client.upsert(
                    self.collection, points=points[start : start + WRITE_BATCH_POINTS], wait=True
                )

    async def delete_document(self, doc_id: str) -> None:
        await self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                )
            ),
            wait=True,
        )

    async def dense(self, vector: list[float], filters: models.Filter, limit: int = 50):
        result = await self.client.query_points(
            self.collection, query=vector, using="dense", query_filter=filters, limit=limit, with_payload=True
        )
        return result.points

    async def keyword(self, query: str, filters: models.Filter, limit: int = 50):
        vector = bm25_vector(query, query=True)
        if not vector.indices:
            return []
        result = await self.client.query_points(
            self.collection,
            query=vector,
            using="sparse",
            query_filter=filters,
            limit=limit,
            with_payload=True,
        )
        return result.points
