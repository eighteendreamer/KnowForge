from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import Session, SuperAdmin
from app.core.cache import bump_revision, revision
from app.core.config import Settings
from app.core.errors import AppError, success
from app.models import (
    Document,
    EvaluationRun,
    IndexRebuild,
    ProcessingTask,
    RuntimeConfiguration,
    RuntimeState,
)
from app.schemas.system import (
    EVALUATION_TARGETS,
    EvaluationInput,
    RebuildInput,
    SettingsPatch,
)
from app.services.audit import record_audit
from app.services.runtime_config import (
    EDITABLE_FIELDS,
    REBUILD_EDITABLE,
    active_row,
    active_settings,
    build_settings,
    configuration_values,
    evaluation_view,
    index_fingerprint,
    publish_settings,
    rebuild_view,
)
from app.services.storage.qdrant_store import QdrantStore
from app.services.task_queue import dispatch_named, require_consumer

router = APIRouter(prefix="/v1/admin/system", tags=["系统设置"])


async def settings_view(session, defaults: Settings) -> dict:
    config = await active_settings(session, defaults)
    return {
        "configuration_id": str(config.configuration_id),
        "index_fingerprint": config.index_fingerprint,
        "values": configuration_values(config),
        "editable_fields": sorted(EDITABLE_FIELDS),
        "rebuild_fields": sorted(REBUILD_EDITABLE),
        "models_configured": defaults.models_configured,
    }


async def open_rebuild(session) -> IndexRebuild | None:
    return await session.scalar(
        select(IndexRebuild)
        .where(IndexRebuild.status.in_(["pending", "running", "evaluating", "ready"]))
        .order_by(IndexRebuild.created_at.desc())
        .limit(1)
    )


@router.get("/settings")
async def read_settings(request: Request, session: Session, user: SuperAdmin):
    view = await settings_view(session, request.app.state.settings)
    view["rebuilds"] = [
        rebuild_view(row)
        for row in await session.scalars(
            select(IndexRebuild).order_by(IndexRebuild.created_at.desc()).limit(20)
        )
    ]
    view["evaluations"] = [
        evaluation_view(row)
        for row in await session.scalars(select(EvaluationRun).order_by(EvaluationRun.id.desc()).limit(20))
    ]
    return success(view)


@router.put("/settings")
async def update_settings(body: SettingsPatch, request: Request, session: Session, user: SuperAdmin):
    config = await publish_settings(
        session, request.app.state.settings, body.values, body.expected_configuration_id, user.id
    )
    record_audit(
        session,
        request,
        user,
        "update",
        "system_settings",
        str(config.configuration_id),
        {"fields": sorted(body.values)},
    )
    await session.commit()
    return success(await settings_view(session, request.app.state.settings))


@router.get("/settings/history/{configuration_id}")
async def read_configuration(configuration_id: UUID, session: Session, user: SuperAdmin):
    row = await session.get(RuntimeConfiguration, configuration_id)
    if row is None:
        raise AppError(404, 1001, "配置版本不存在")
    return success(
        {
            "configuration_id": str(row.id),
            "index_fingerprint": row.index_fingerprint,
            "values": row.values,
        }
    )


@router.post("/rebuilds")
async def start_rebuild(body: RebuildInput, request: Request, session: Session, user: SuperAdmin):
    await require_consumer(request.app.state.celery, session)
    current = await active_settings(session, request.app.state.settings)
    candidate = current.model_copy(
        update={"embedding_model": body.embedding_model, "embedding_dimension": body.embedding_dimension}
    )
    fingerprint = index_fingerprint(candidate)
    if fingerprint == current.index_fingerprint:
        raise AppError(409, 1001, "目标模型与维度与当前索引一致，无需重建")
    collection = "knowforge_rebuild_" + uuid4().hex
    if await open_rebuild(session) is not None:
        raise AppError(409, 1001, "已有重建流程未结束，请先完成或取消")
    pending = await session.scalar(
        select(func.count())
        .select_from(ProcessingTask)
        .where(ProcessingTask.status.in_(["pending", "running"]))
    )
    if pending:
        raise AppError(409, 1001, f"仍有 {pending} 个文档任务未结束，请等待完成后重建")
    total_chunks = await session.scalar(
        select(func.coalesce(func.sum(Document.total_chunks), 0)).where(Document.status == "ready")
    )
    if not total_chunks:
        raise AppError(409, 1001, "当前没有可重建的已就绪文档")
    gateway = request.app.state.models.for_config(candidate)
    # The probe is a remote model call that can take tens of seconds; close the read transaction first.
    await session.commit()
    probe = await gateway.embed(["维度探测"], "online")
    if not probe or len(probe[0]) != candidate.embedding_dimension:
        raise AppError(
            409,
            1001,
            f"模型实际返回维度 {len(probe[0]) if probe else 0} 与声明的 {candidate.embedding_dimension} 不一致",
        )
    values = {**configuration_values(candidate), "qdrant_collection": collection}
    target = RuntimeConfiguration(
        id=uuid4(), values=values, index_fingerprint=fingerprint, created_by=user.id
    )
    session.add(target)
    await session.flush()
    rebuild = IndexRebuild(
        id=uuid4(),
        source_configuration_id=current.configuration_id,
        target_configuration_id=target.id,
        target_collection=collection,
        target_values=values,
        target_fingerprint=fingerprint,
        source_revision=await revision(session),
        total_documents=await session.scalar(
            select(func.count()).select_from(Document).where(Document.status == "ready")
        ),
    )
    session.add(rebuild)
    await session.flush()
    record_audit(
        session,
        request,
        user,
        "rebuild",
        "index",
        str(rebuild.id),
        {
            "embedding_model": candidate.embedding_model,
            "embedding_dimension": candidate.embedding_dimension,
        },
    )
    view = rebuild_view(rebuild)
    await session.commit()
    await dispatch_named(request.app.state.celery, session, "knowforge.rebuild", [str(rebuild.id)])
    return success(view)


@router.get("/rebuilds")
async def list_rebuilds(session: Session, user: SuperAdmin):
    rows = await session.scalars(select(IndexRebuild).order_by(IndexRebuild.created_at.desc()).limit(50))
    return success({"items": [rebuild_view(row) for row in rows]})


async def require_rebuild(session, rebuild_id: UUID) -> IndexRebuild:
    row = await session.get(IndexRebuild, rebuild_id)
    if row is None:
        raise AppError(404, 1001, "重建任务不存在")
    return row


@router.post("/rebuilds/{rebuild_id}/retry")
async def retry_rebuild(rebuild_id: UUID, request: Request, session: Session, user: SuperAdmin):
    row = await require_rebuild(session, rebuild_id)
    if row.status != "failed":
        raise AppError(400, 1001, "仅失败的重建任务可重试")
    if await open_rebuild(session) is not None:
        raise AppError(409, 1001, "已有重建流程未结束")
    # Gate before the status write: a refused retry must leave the failed row intact, not pending with no consumer.
    await require_consumer(request.app.state.celery, session)
    row.status, row.error_message = "pending", None
    record_audit(session, request, user, "retry", "index_rebuild", str(row.id))
    view = rebuild_view(row)
    await session.commit()
    await dispatch_named(request.app.state.celery, session, "knowforge.rebuild", [str(row.id)])
    return success(view)


@router.delete("/rebuilds/{rebuild_id}")
async def cancel_rebuild(rebuild_id: UUID, request: Request, session: Session, user: SuperAdmin):
    row = await require_rebuild(session, rebuild_id)
    if row.status in {"switched"}:
        raise AppError(400, 1001, "已切换的重建任务不能取消")
    if row.status == "running":
        raise AppError(400, 1001, "重建正在执行，请等待本轮结束或停止 Worker 后再取消")
    if await request.app.state.qdrant.collection_exists(row.target_collection):
        await request.app.state.qdrant.delete_collection(row.target_collection)
    row.status = "cancelled"
    record_audit(session, request, user, "cancel", "index_rebuild", str(row.id))
    view = rebuild_view(row)
    await session.commit()
    return success(view)


@router.post("/evaluations")
async def record_run(body: EvaluationInput, request: Request, session: Session, user: SuperAdmin):
    configuration = await session.get(RuntimeConfiguration, body.configuration_id)
    if configuration is None:
        raise AppError(404, 1001, "配置版本不存在")
    metrics = body.metrics.model_dump()
    passed = all(metrics[key] >= value for key, value in EVALUATION_TARGETS.items())
    run = EvaluationRun(
        name=body.name,
        dataset_version=body.dataset_version,
        dataset_kind=body.dataset_kind,
        dataset_hash=body.dataset_hash,
        configuration_id=body.configuration_id,
        collection=configuration.values["qdrant_collection"],
        metrics=metrics,
        passed=passed,
        notes=body.notes,
        created_by=user.id,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    open_rebuild = await session.scalar(
        select(IndexRebuild)
        .where(
            IndexRebuild.target_configuration_id == body.configuration_id,
            IndexRebuild.status.in_(["evaluating", "ready"]),
        )
        .order_by(IndexRebuild.created_at.desc())
        .limit(1)
    )
    if open_rebuild is not None:
        open_rebuild.evaluation_id = run.id
        open_rebuild.status = "ready" if run.passed else "evaluating"
    record_audit(session, request, user, "create", "evaluation_run", run.id, {"passed": passed})
    view = evaluation_view(run)
    await session.commit()
    return success(view)


@router.get("/evaluations")
async def list_runs(session: Session, user: SuperAdmin):
    rows = await session.scalars(select(EvaluationRun).order_by(EvaluationRun.id.desc()).limit(50))
    return success({"items": [evaluation_view(row) for row in rows]})


@router.post("/rebuilds/{rebuild_id}/switch")
async def switch_rebuild(rebuild_id: UUID, request: Request, session: Session, user: SuperAdmin):
    row = await session.scalar(select(IndexRebuild).where(IndexRebuild.id == rebuild_id).with_for_update())
    if row is None:
        raise AppError(404, 1001, "重建任务不存在")
    if row.status not in {"evaluating", "ready"}:
        raise AppError(400, 1001, f"重建当前状态为 {row.status}，不能切换")
    if row.failed_documents:
        raise AppError(409, 1001, f"{row.failed_documents} 篇文档重建失败，禁止切换")
    run = await session.get(EvaluationRun, row.evaluation_id) if row.evaluation_id else None
    if run is None:
        raise AppError(409, 1001, "切换前必须记录该索引版本的评估结果")
    if not run.passed or run.dataset_kind != "frozen":
        raise AppError(409, 1001, "冻结验收集评估未达标，禁止切换在线索引")
    if run.configuration_id != row.target_configuration_id:
        raise AppError(409, 1001, "评估结果不属于该索引版本")
    if run.collection != row.target_collection:
        raise AppError(409, 1001, "评估结果对应的 Collection 与重建目标不一致")
    current = await active_row(session)
    if current.id != row.source_configuration_id:
        raise AppError(409, 1001, "在线配置已变化，请基于最新版本重新重建")
    if await revision(session) != row.finish_revision:
        raise AppError(409, 1001, "重建期间知识库发生变化，请重跑重建后再切换")
    pending = await session.scalar(
        select(func.count())
        .select_from(ProcessingTask)
        .where(ProcessingTask.status.in_(["pending", "running"]))
    )
    if pending:
        raise AppError(409, 1001, f"仍有 {pending} 个文档任务未结束，请等待追平后再切换")
    state = await session.scalar(select(RuntimeState).where(RuntimeState.id == 1).with_for_update())
    target = await session.get(RuntimeConfiguration, row.target_configuration_id)
    if state is None or target is None:
        raise AppError(503, 5002, "运行配置状态不完整，禁止切换")
    target_settings = build_settings(
        request.app.state.settings, target.values, target.index_fingerprint, target.id
    )
    if target_settings.index_fingerprint != row.target_fingerprint:
        raise AppError(409, 1001, "目标配置指纹与重建记录不一致")
    try:
        await QdrantStore(request.app.state.qdrant, target_settings).ensure_collection()
    except ValueError as exc:
        raise AppError(409, 1001, f"目标索引校验失败：{exc}") from None
    state.configuration_id = row.target_configuration_id
    row.status, row.finished_at = "switched", datetime.now(UTC)
    await bump_revision(session)
    record_audit(
        session,
        request,
        user,
        "switch",
        "index_rebuild",
        str(row.id),
        {
            "collection": row.target_collection,
            "evaluation_id": row.evaluation_id,
        },
    )
    await session.commit()
    return success({"status": "switched", **await settings_view(session, request.app.state.settings)})
