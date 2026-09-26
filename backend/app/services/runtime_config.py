import hashlib
import json
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import bump_revision
from app.core.config import Settings
from app.core.errors import AppError
from app.models import EvaluationRun, IndexRebuild, RuntimeConfiguration, RuntimeState

RUNTIME_FIELDS = {
    "model_api_base_url",
    "model_rerank_path",
    "model_timeout_seconds",
    "model_max_retries",
    "model_max_concurrency",
    "model_requests_per_minute",
    "embedding_model",
    "embedding_dimension",
    "rerank_model",
    "llm_model",
    "vl_model",
    "rerank_enabled",
    "qdrant_url",
    "qdrant_collection",
    "storage_path",
    "upload_max_bytes",
    "pdf_max_pages",
    "pdf_max_pixels",
    "tokenizer_path",
    "embedding_max_tokens",
    "embedding_batch_size",
    "chunk_size",
    "chunk_overlap",
    "cache_ttl_seconds",
    "tag_auto_approve_confidence",
}
# Vectors in an index are only interchangeable when every field here matches.
INDEX_FIELDS = {
    "model_api_base_url",
    "embedding_model",
    "embedding_dimension",
    "embedding_max_tokens",
    "chunk_size",
    "chunk_overlap",
    "tokenizer_path",
}
# A rebuild re-embeds stored chunks, so parsing and chunking must not move.
REBUILD_EDITABLE = {"embedding_model", "embedding_dimension"}
EDITABLE_FIELDS = RUNTIME_FIELDS - INDEX_FIELDS - {"qdrant_collection", "storage_path"}
TEXT_PREPROCESSING = "structured-context-v1"
SPARSE_ENCODING = "jieba-bm25-v1"


@lru_cache(maxsize=32)
def _tokenizer_digest(path: str, modified: int, size: int) -> str:
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def index_identity(settings: Settings) -> dict:
    path = Path(settings.tokenizer_path).resolve()
    stat = path.stat()
    return {
        **settings.model_dump(mode="json", include=INDEX_FIELDS),
        "tokenizer_sha256": _tokenizer_digest(str(path), stat.st_mtime_ns, stat.st_size),
        "text_preprocessing": TEXT_PREPROCESSING,
        "sparse_encoding": SPARSE_ENCODING,
    }


def index_fingerprint(settings: Settings) -> str:
    payload = json.dumps(index_identity(settings), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def configuration_values(settings: Settings) -> dict:
    return settings.model_dump(mode="json", include=RUNTIME_FIELDS)


def build_settings(defaults: Settings, values: dict, fingerprint: str, configuration_id: UUID) -> Settings:
    if not isinstance(values, dict) or not isinstance(fingerprint, str):
        raise AppError(503, 5002, "持久化运行配置格式无效")
    try:
        config = Settings.model_validate({**defaults.model_dump(), **values})
    except ValidationError:
        raise AppError(503, 5002, "持久化运行配置无效") from None
    if configuration_values(config) != values:
        raise AppError(503, 5002, "持久化运行配置与当前字段集不一致，请重新迁移")
    if index_fingerprint(config) != fingerprint:
        raise AppError(503, 5002, "模型、分块或分词器已变化，禁止混用向量，请全量重建")
    return config.model_copy(update={"configuration_id": configuration_id, "index_fingerprint": fingerprint})


def task_snapshot(settings: Settings) -> dict:
    if settings.configuration_id is None or settings.index_fingerprint is None:
        raise AppError(503, 5002, "运行配置尚未初始化")
    return {
        **configuration_values(settings),
        "configuration_id": str(settings.configuration_id),
        "index_fingerprint": settings.index_fingerprint,
    }


def restore_snapshot(defaults: Settings, snapshot: dict) -> Settings:
    if set(snapshot) != RUNTIME_FIELDS | {"configuration_id", "index_fingerprint"}:
        raise AppError(503, 5002, "任务配置快照不完整，请在迁移后重新提交任务")
    return build_settings(
        defaults,
        {key: snapshot[key] for key in RUNTIME_FIELDS},
        snapshot["index_fingerprint"],
        UUID(snapshot["configuration_id"]),
    )


async def active_row(session: AsyncSession) -> RuntimeConfiguration:
    row = await session.scalar(
        select(RuntimeConfiguration)
        .join(RuntimeState, RuntimeState.configuration_id == RuntimeConfiguration.id)
        .where(RuntimeState.id == 1)
    )
    if row is None:
        raise AppError(503, 5002, "运行配置尚未初始化")
    return row


async def active_settings(session: AsyncSession, defaults: Settings) -> Settings:
    row = await active_row(session)
    return build_settings(defaults, row.values, row.index_fingerprint, row.id)


async def initialize_runtime(session: AsyncSession, defaults: Settings) -> Settings:
    await session.execute(text("SELECT pg_advisory_xact_lock(734603)"))
    if await session.get(RuntimeState, 1) is None:
        row = RuntimeConfiguration(
            id=uuid4(),
            values=configuration_values(defaults),
            index_fingerprint=index_fingerprint(defaults),
        )
        session.add(row)
        await session.flush()
        session.add(RuntimeState(id=1, configuration_id=row.id))
        await session.flush()
    return await active_settings(session, defaults)


async def publish_settings(
    session: AsyncSession, defaults: Settings, values: dict, expected: UUID, user_id: int
) -> Settings:
    if not set(values) <= EDITABLE_FIELDS:
        raise AppError(400, 1002, "只能修改不影响向量空间的非敏感配置")
    state = await session.scalar(select(RuntimeState).where(RuntimeState.id == 1).with_for_update())
    if state is None or state.configuration_id != expected:
        raise AppError(409, 1001, "配置版本已变化，请刷新后重试")
    current = await active_settings(session, defaults)
    try:
        candidate = Settings.model_validate({**current.model_dump(), **values})
    except ValidationError:
        raise AppError(400, 1002, "配置字段类型、范围或组合无效") from None
    if configuration_values(candidate) == configuration_values(current):
        return current
    row = RuntimeConfiguration(
        id=uuid4(),
        values=configuration_values(candidate),
        index_fingerprint=current.index_fingerprint,
        created_by=user_id,
    )
    session.add(row)
    await session.flush()
    state.configuration_id = row.id
    await bump_revision(session)
    return build_settings(defaults, row.values, row.index_fingerprint, row.id)


def rebuild_view(rebuild: IndexRebuild) -> dict:
    return {
        "id": str(rebuild.id),
        "source_configuration_id": str(rebuild.source_configuration_id),
        "target_configuration_id": str(rebuild.target_configuration_id)
        if rebuild.target_configuration_id
        else None,
        "target_collection": rebuild.target_collection,
        "status": rebuild.status,
        "total_documents": rebuild.total_documents,
        "completed_documents": rebuild.completed_documents,
        "failed_documents": rebuild.failed_documents,
        "evaluation_id": rebuild.evaluation_id,
        "error_message": rebuild.error_message,
        "created_at": rebuild.created_at,
        "updated_at": rebuild.updated_at,
    }


def evaluation_view(run: EvaluationRun) -> dict:
    return {
        "id": run.id,
        "name": run.name,
        "dataset_version": run.dataset_version,
        "dataset_kind": run.dataset_kind,
        "configuration_id": str(run.configuration_id),
        "collection": run.collection,
        "metrics": run.metrics,
        "passed": run.passed,
        "notes": run.notes,
        "created_at": run.created_at,
    }
