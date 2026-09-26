import json
from uuid import UUID, uuid4

import httpx
import pytest
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from sqlalchemy import func, select
from test_models import mock_gateway

from app.core.config import Settings
from app.core.errors import AppError
from app.core.model_client import ModelGateway
from app.models import Document, IndexRebuild, ProcessingTask, RuntimeConfiguration
from app.schemas.search import SearchInput
from app.services.pipeline import new_task
from app.services.retrieval.search_service import search
from app.services.runtime_config import active_settings, restore_snapshot, task_snapshot
from app.services.storage.qdrant_store import QdrantStore


async def get_settings(client, headers):
    response = await client.get("/v1/admin/system/settings", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_system_settings_are_super_admin_only_and_seeded_from_environment(context):
    data = await get_settings(context["client"], context["admin_headers"])
    assert UUID(data["configuration_id"]) == context["runtime"].configuration_id
    assert data["index_fingerprint"] == context["runtime"].index_fingerprint
    assert data["values"]["embedding_model"] == context["settings"].embedding_model
    assert "model_api_key" not in json.dumps(data)
    blocked = await context["client"].get("/v1/admin/system/settings", headers=context["editor_headers"])
    assert blocked.status_code == 403


async def test_publish_rotates_configuration_but_refuses_index_and_location_changes(context):
    client, headers = context["client"], context["admin_headers"]
    original = await get_settings(client, headers)
    for field, value in [
        ("embedding_model", "Qwen/Qwen3-Embedding-0.6B"),
        ("embedding_dimension", 1024),
        ("chunk_size", 500),
        ("qdrant_collection", "x"),
        ("storage_path", "x"),
    ]:
        refused = await client.put(
            "/v1/admin/system/settings",
            headers=headers,
            json={"expected_configuration_id": original["configuration_id"], "values": {field: value}},
        )
        assert refused.status_code == 400, refused.text
        assert "不影响向量空间" in refused.json()["message"]
    updated = await client.put(
        "/v1/admin/system/settings",
        headers=headers,
        json={
            "expected_configuration_id": original["configuration_id"],
            "values": {"rerank_enabled": False, "cache_ttl_seconds": 60},
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["values"]["rerank_enabled"] is False
    assert updated.json()["data"]["configuration_id"] != original["configuration_id"]
    stale = await client.put(
        "/v1/admin/system/settings",
        headers=headers,
        json={"expected_configuration_id": original["configuration_id"], "values": {"cache_ttl_seconds": 90}},
    )
    assert stale.status_code == 409
    async with context["sessions"]() as session:
        rows = list(await session.scalars(select(RuntimeConfiguration)))
        assert len(rows) == 2
        assert {row.index_fingerprint for row in rows} == {original["index_fingerprint"]}


async def test_task_snapshot_replays_pinned_configuration_and_detects_index_tampering(context):
    async with context["sessions"]() as session:
        document = Document(
            doc_id="doc_" + uuid4().hex,
            title="Snapshot",
            original_filename="snapshot.html",
            file_type="html",
            file_size=10,
            source_path="documents/snapshot.html",
        )
        session.add(document)
        await session.flush()
        task = new_task(document, context["runtime"])
        session.add(task)
        await session.commit()
        assert task.config_snapshot["configuration_id"] == str(context["runtime"].configuration_id)
        assert "model_api_key" not in task.config_snapshot
        assert restore_snapshot(context["settings"], task.config_snapshot) == context["runtime"]
        tampered = {**task.config_snapshot, "embedding_model": "Qwen/Qwen3-Embedding-0.6B"}
        with pytest.raises(AppError, match="禁止混用向量"):
            restore_snapshot(context["settings"], tampered)
        with pytest.raises(AppError, match="不完整"):
            restore_snapshot(context["settings"], {k: v for k, v in tampered.items() if k != "chunk_size"})


shadow_collections: list[str] = []


@pytest.fixture(autouse=True)
def drop_shadow_collections():
    # A successful switch really creates the shadow collection, so the suite has to remove it again.
    yield
    client = QdrantClient(url=Settings().qdrant_url)
    for name in shadow_collections:
        if client.collection_exists(name):
            client.delete_collection(name, timeout=60)
    client.close()
    shadow_collections.clear()


async def make_rebuild(context, *, changed=True) -> tuple[IndexRebuild, dict]:
    from app.core.cache import bump_revision, revision
    from app.services.runtime_config import configuration_values, index_fingerprint

    async with context["sessions"]() as session:
        source = await session.get(RuntimeConfiguration, context["runtime"].configuration_id)
        values = {**configuration_values(context["runtime"])}
        collection = "knowforge_shadow_" + uuid4().hex
        shadow_collections.append(collection)
        values["qdrant_collection"] = collection
        candidate = context["settings"].model_copy(update={"qdrant_collection": collection})
        target = RuntimeConfiguration(
            id=uuid4(), values=values, index_fingerprint=index_fingerprint(candidate)
        )
        session.add(target)
        await session.flush()
        finish_revision = await revision(session)
        if changed:
            await bump_revision(session)
        rebuild = IndexRebuild(
            id=uuid4(),
            source_configuration_id=source.id,
            target_configuration_id=target.id,
            target_collection=collection,
            target_values=values,
            target_fingerprint=target.index_fingerprint,
            status="evaluating",
            source_revision="0",
            finish_revision=finish_revision,
        )
        session.add(rebuild)
        await session.commit()
        return rebuild, {"collection": collection, "target_id": str(target.id)}


def evaluation_payload(target_id: str, **overrides) -> dict:
    body = {
        "name": "冻结验收集",
        "dataset_version": "frozen-2026-09",
        "dataset_kind": "frozen",
        "dataset_hash": "a" * 64,
        "configuration_id": target_id,
        "metrics": {
            "recall_at_5": 0.9,
            "recall_at_10": 0.9,
            "mrr": 0.82,
            "ndcg_at_10": 0.86,
            "precision_at_5": 0.84,
        },
    }
    body.update(overrides)
    return body


async def attach_evaluation(context, rebuild_id, run_id) -> None:
    async with context["sessions"]() as session:
        row = await session.get(IndexRebuild, rebuild_id)
        row.evaluation_id = run_id
        await session.commit()


async def test_switch_requires_passing_frozen_evaluation_before_going_live(context):
    client, headers = context["client"], context["admin_headers"]
    rebuild, meta = await make_rebuild(context, changed=False)
    switch = lambda: client.post(f"/v1/admin/system/rebuilds/{rebuild.id}/switch", headers=headers)  # noqa: E731
    assert "评估" in (await switch()).json()["message"]

    below = await client.post(
        "/v1/admin/system/evaluations",
        headers=headers,
        json=evaluation_payload(
            meta["target_id"],
            metrics={
                "recall_at_5": 0.5,
                "recall_at_10": 0.5,
                "mrr": 0.5,
                "ndcg_at_10": 0.5,
                "precision_at_5": 0.5,
            },
        ),
    )
    assert below.json()["data"]["passed"] is False
    await attach_evaluation(context, rebuild.id, below.json()["data"]["id"])
    assert "达标" in (await switch()).json()["message"]

    tuning = await client.post(
        "/v1/admin/system/evaluations",
        headers=headers,
        json=evaluation_payload(meta["target_id"], dataset_kind="tuning"),
    )
    assert tuning.json()["data"]["passed"] is True
    await attach_evaluation(context, rebuild.id, tuning.json()["data"]["id"])
    assert "冻结" in (await switch()).json()["message"]

    frozen = await client.post(
        "/v1/admin/system/evaluations", headers=headers, json=evaluation_payload(meta["target_id"])
    )
    await attach_evaluation(context, rebuild.id, frozen.json()["data"]["id"])
    shadow = context["runtime"].model_copy(update={"qdrant_collection": meta["collection"]})
    await QdrantStore(context["app"].state.qdrant, shadow).ensure_collection()
    switched = await switch()
    assert switched.status_code == 200, switched.text
    assert switched.json()["data"]["values"]["qdrant_collection"] == meta["collection"]
    async with context["sessions"]() as session:
        current = await active_settings(session, context["settings"])
        assert current.qdrant_collection == meta["collection"]
        empty = await search(
            session,
            SearchInput(query="Redis", search_type="keyword", options={"rerank": False}),
            current,
            context["app"].state.models.for_config(current),
            QdrantStore(context["app"].state.qdrant, current),
            context["app"].state.redis,
            public_only=True,
        )
    assert empty["results"] == []


async def test_switch_refuses_when_knowledge_changed_during_rebuild(context):
    rebuild, meta = await make_rebuild(context, changed=True)
    recorded = await context["client"].post(
        "/v1/admin/system/evaluations",
        headers=context["admin_headers"],
        json=evaluation_payload(meta["target_id"]),
    )
    assert recorded.json()["data"]["passed"] is True
    await attach_evaluation(context, rebuild.id, recorded.json()["data"]["id"])
    response = await context["client"].post(
        f"/v1/admin/system/rebuilds/{rebuild.id}/switch", headers=context["admin_headers"]
    )
    assert response.status_code == 409
    assert "发生变化" in response.json()["message"]


async def test_recording_evaluation_links_the_open_rebuild_and_gate_still_refuses(context):
    rebuild, meta = await make_rebuild(context, changed=False)
    below = await context["client"].post(
        "/v1/admin/system/evaluations",
        headers=context["admin_headers"],
        json=evaluation_payload(
            meta["target_id"],
            metrics={
                "recall_at_5": 0.5,
                "recall_at_10": 0.5,
                "mrr": 0.5,
                "ndcg_at_10": 0.5,
                "precision_at_5": 0.5,
            },
        ),
    )
    assert below.status_code == 200
    async with context["sessions"]() as session:
        row = await session.get(IndexRebuild, rebuild.id)
        assert row.evaluation_id == below.json()["data"]["id"]
        assert row.status == "evaluating"
    response = await context["client"].post(
        f"/v1/admin/system/rebuilds/{rebuild.id}/switch", headers=context["admin_headers"]
    )
    assert response.status_code == 409 and "达标" in response.json()["message"]


async def test_rebuild_start_checks_quiet_queue_and_real_embedding_dimension(context):
    client, headers = context["client"], context["admin_headers"]
    async with context["sessions"]() as session:
        document = Document(
            doc_id="doc_" + uuid4().hex,
            title="Rebuild corpus",
            original_filename="rebuild.html",
            file_type="html",
            file_size=10,
            source_path="documents/rebuild.html",
            status="ready",
            total_chunks=2,
        )
        session.add(document)
        await session.commit()

    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": body["model"],
                "data": [{"index": 0, "object": "embedding", "embedding": [0.1] * 1024}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    context["app"].state.models = mock_gateway(context, handler)
    mismatch = await client.post(
        "/v1/admin/system/rebuilds",
        headers=headers,
        json={"embedding_model": "Qwen/Qwen3-Embedding-8B", "embedding_dimension": 4096},
    )
    assert mismatch.status_code == 409 and "维度" in mismatch.json()["message"]

    async with context["sessions"]() as session:
        session.add(
            ProcessingTask(
                id=uuid4(),
                doc_id=document.id,
                task_type="ingest",
                config_snapshot=task_snapshot(context["runtime"]),
            )
        )
        await session.commit()
    busy = await client.post(
        "/v1/admin/system/rebuilds",
        headers=headers,
        json={"embedding_model": "other/model", "embedding_dimension": 1024},
    )
    assert busy.status_code == 409 and "文档任务" in busy.json()["message"]

    async with context["sessions"]() as session:
        await session.execute(ProcessingTask.__table__.delete())
        await session.commit()
    started = await client.post(
        "/v1/admin/system/rebuilds",
        headers=headers,
        json={"embedding_model": "other/model", "embedding_dimension": 1024},
    )
    assert started.status_code == 200, started.text
    data = started.json()["data"]
    assert data["status"] == "pending" and "queued" not in data
    async with context["sessions"]() as session:
        row = await session.get(IndexRebuild, UUID(data["id"]))
        target = await session.get(RuntimeConfiguration, row.target_configuration_id)
        assert row.target_collection == data["target_collection"]
        assert target.values["qdrant_collection"] == row.target_collection
        assert target.index_fingerprint == row.target_fingerprint
    duplicate = await client.post(
        "/v1/admin/system/rebuilds",
        headers=headers,
        json={"embedding_model": "other/model", "embedding_dimension": 1024},
    )
    assert duplicate.status_code == 409 and "重建流程" in duplicate.json()["message"]
    cancelled = await client.delete(f"/v1/admin/system/rebuilds/{data['id']}", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"


async def test_evaluation_gate_keys_on_recall_at_depth_ten_and_never_defaults_it(context):
    client, headers = context["client"], context["admin_headers"]
    _, meta = await make_rebuild(context, changed=False)
    shallow = evaluation_payload(
        meta["target_id"],
        metrics={
            "recall_at_5": 0.4,
            "recall_at_10": 0.9,
            "mrr": 0.9,
            "ndcg_at_10": 0.9,
            "precision_at_5": 0.9,
        },
    )
    recorded = await client.post("/v1/admin/system/evaluations", headers=headers, json=shallow)
    assert recorded.json()["data"]["passed"] is True

    incomplete = evaluation_payload(meta["target_id"])
    del incomplete["metrics"]["recall_at_10"]
    refused = await client.post("/v1/admin/system/evaluations", headers=headers, json=incomplete)
    assert refused.status_code == 400 and refused.json()["code"] == 1001


async def test_rebuild_submission_is_refused_when_no_worker_consumes_the_queue(context, monkeypatch):
    """The gate must fire before the multi-second dimension probe, or a dead worker still costs a paid call."""
    from app.services import task_queue

    def never_called(request):
        raise AssertionError("没有消费者时不该发起远程维度探测")

    context["app"].state.models = mock_gateway(context, never_called)

    async def offline(celery, session):
        return False

    monkeypatch.setattr(task_queue, "consumer_online", offline)
    client, headers = context["client"], context["admin_headers"]
    async with context["sessions"]() as session:
        rebuilds = await session.scalar(select(func.count()).select_from(IndexRebuild))
        configurations = await session.scalar(select(func.count()).select_from(RuntimeConfiguration))
    refused = await client.post(
        "/v1/admin/system/rebuilds",
        headers=headers,
        json={"embedding_model": "other/model", "embedding_dimension": 1024},
    )
    assert refused.status_code == 409 and "Worker" in refused.json()["message"]
    async with context["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(IndexRebuild)) == rebuilds
        assert await session.scalar(select(func.count()).select_from(RuntimeConfiguration)) == configurations


async def test_retry_rebuild_stays_failed_when_no_worker_consumes_the_queue(context, monkeypatch):
    """A refused retry must leave the row where it was; writing `pending` first would strand a task nobody runs."""
    from app.services import task_queue

    rebuild, _ = await make_rebuild(context, changed=False)
    async with context["sessions"]() as session:
        row = await session.get(IndexRebuild, rebuild.id)
        row.status = "failed"
        await session.commit()

    async def offline(celery, session):
        return False

    monkeypatch.setattr(task_queue, "consumer_online", offline)
    refused = await context["client"].post(
        f"/v1/admin/system/rebuilds/{rebuild.id}/retry", headers=context["admin_headers"]
    )
    assert refused.status_code == 409 and "Worker" in refused.json()["message"]
    async with context["sessions"]() as session:
        assert (await session.get(IndexRebuild, rebuild.id)).status == "failed"


async def test_model_gateway_reuses_transport_for_pinned_configuration(context):
    gateway = ModelGateway(
        context["settings"],
        context["app"].state.redis,
        AsyncOpenAI(api_key="test-only", base_url="https://model.test/v1"),
    )
    try:
        child = gateway.for_config(context["runtime"].model_copy(update={"rerank_model": "other/reranker"}))
        assert child.client is gateway.client
        assert child.settings.rerank_model == "other/reranker"
        assert child.prefix == gateway.prefix
        await child.close()
        assert gateway.client is not None
    finally:
        await gateway.close()
