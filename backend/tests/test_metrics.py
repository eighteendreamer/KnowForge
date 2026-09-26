import asyncio
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from prometheus_client import generate_latest
from pydantic import SecretStr
from sqlalchemy import select
from test_models import mock_gateway

from app.core.errors import AppError
from app.core.metrics import MODEL_REQUESTS, MODEL_TOKENS, MODEL_WAIT, REGISTRY, record_usage
from app.models import Document, ProcessingTask


async def test_metrics_disabled_without_token_and_protected_when_enabled(context):
    client = context["client"]
    context["settings"].metrics_token = SecretStr("")
    assert (await client.get("/metrics")).status_code == 404
    context["settings"].metrics_token = SecretStr("metrics-test-secret")
    for headers in [{}, context["admin_headers"], {"Authorization": "Bearer wrong"}]:
        assert (await client.get("/metrics", headers=headers)).status_code == 401
    response = await client.get("/metrics", headers={"Authorization": "Bearer metrics-test-secret"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'knowforge_tasks{status="pending"}' in response.text
    assert "metrics-test-secret" not in response.text
    assert context["settings"].model_api_key.get_secret_value() not in response.text


async def test_metrics_report_queue_consumer_state_and_stuck_rebuild_age(context, monkeypatch):
    """A dead worker must be visible in metrics as well as in the 409s, because nothing else watches rebuild queueing."""
    from datetime import UTC, datetime, timedelta

    from app.core import metrics as metrics_module
    from app.models import IndexRebuild, RuntimeConfiguration

    context["settings"].metrics_token = SecretStr("metrics-test-secret")
    headers = {"Authorization": "Bearer metrics-test-secret"}

    async def online(celery, session):
        return True

    monkeypatch.setattr(metrics_module, "consumer_online", online)
    assert (
        "knowforge_queue_consumer_online 1" in (await context["client"].get("/metrics", headers=headers)).text
    )

    async def offline(celery, session):
        return False

    monkeypatch.setattr(metrics_module, "consumer_online", offline)
    async with context["sessions"]() as session:
        configuration = await session.scalar(select(RuntimeConfiguration).limit(1))
        session.add(
            IndexRebuild(
                id=uuid4(),
                source_configuration_id=configuration.id,
                target_configuration_id=configuration.id,
                target_collection="knowforge_rebuild_stuck_" + uuid4().hex,
                target_values={},
                target_fingerprint="f" * 64,
                source_revision="probe",
                status="pending",
                created_at=datetime.now(UTC) - timedelta(minutes=30),
            )
        )
        await session.commit()
    body = (await context["client"].get("/metrics", headers=headers)).text
    assert "knowforge_queue_consumer_online 0" in body
    assert any(
        line.startswith("knowforge_oldest_pending_rebuild_seconds") and float(line.split()[-1]) > 1700
        for line in body.splitlines()
    )


async def test_metrics_use_route_templates_and_persisted_queue_state(context):
    client = context["client"]
    context["settings"].metrics_token = SecretStr("metrics-test-secret")
    async with context["sessions"]() as session:
        doc = Document(
            doc_id="doc_" + uuid4().hex,
            title="Metrics fixture",
            original_filename="metrics.html",
            file_type="html",
            file_size=10,
            source_path="documents/metrics.html",
        )
        session.add(doc)
        await session.flush()
        session.add(ProcessingTask(id=uuid4(), doc_id=doc.id, task_type="ingest", status="pending"))
        await session.commit()
    await client.get("/v1/admin/documents/private-id-never-a-label", headers=context["admin_headers"])
    await client.get("/unique-secret-unmatched-path?key=never-label")
    response = await client.get("/metrics", headers={"Authorization": "Bearer metrics-test-secret"})
    assert 'route="/v1/admin/documents/{doc_id}"' in response.text
    assert 'route="unmatched"' in response.text
    assert 'knowforge_tasks{status="pending"} 1.0' in response.text
    for private in [
        "private-id-never-a-label",
        "unique-secret-unmatched-path",
        "never-label",
        "Metrics fixture",
    ]:
        assert private not in response.text


async def test_model_attempt_and_usage_metrics(context):
    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1] * 4096, "object": "embedding"}],
                "object": "list",
                "model": body["model"],
                "usage": {"prompt_tokens": 7, "total_tokens": 7},
            },
        )

    gateway = mock_gateway(context, handler)
    labels = ("embedding", gateway.settings.embedding_model, "batch", "ok")
    before = MODEL_REQUESTS.labels(*labels)._value.get()
    tokens = MODEL_TOKENS.labels("embedding", gateway.settings.embedding_model, "input")
    tokens_before = tokens._value.get()
    try:
        await gateway.embed(["private-document-content"])
        assert MODEL_REQUESTS.labels(*labels)._value.get() == before + 1
        assert tokens._value.get() == tokens_before + 7
        assert "private-document-content" not in generate_latest(REGISTRY).decode()
    finally:
        await gateway.close()


async def test_model_failure_and_local_quota_are_separate(context):
    gateway = mock_gateway(
        context, lambda _: httpx.Response(429, json={"error": {"message": "private-upstream-text"}})
    )
    model = gateway.settings.embedding_model
    remote = MODEL_REQUESTS.labels("embedding", model, "batch", "429")
    before = remote._value.get()
    try:
        with pytest.raises(AppError):
            await gateway.embed(["query"])
        assert remote._value.get() == before + 1
        await context["app"].state.redis.hset(
            gateway.prefix + "rate:requests", mapping={"tokens": 0, "updated": 9999999999}
        )
        local = MODEL_WAIT.labels("embedding", "batch", "failed")
        wait_before = local._sum.get()
        with pytest.raises(AppError) as error:
            await gateway.embed(["query"])
        assert error.value.status == 429
        assert remote._value.get() == before + 1
        assert local._sum.get() > wait_before
        assert "private-upstream-text" not in generate_latest(REGISTRY).decode()
    finally:
        await gateway.close()


def test_missing_usage_does_not_fabricate_tokens():
    model = "usage-fixture-" + uuid4().hex
    record_usage({"results": []}, "rerank", model)
    assert model not in generate_latest(REGISTRY).decode()


async def test_metrics_aggregate_independent_processes(tmp_path):
    root = Path(__file__).parents[2]
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path), "PYTHONPATH": str(root / "backend")}
    code = "from app.core.metrics import TASK_RUNS; TASK_RUNS.labels('ingest', 'succeeded').inc()"
    for _ in range(2):
        result = await asyncio.to_thread(
            subprocess.run,
            [str(root / ".venv/Scripts/python.exe"), "-c", code],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.returncode == 0
    from prometheus_client import CollectorRegistry, multiprocess

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=str(tmp_path))
    assert (
        registry.get_sample_value("knowforge_task_runs_total", {"type": "ingest", "outcome": "succeeded"})
        == 2
    )
