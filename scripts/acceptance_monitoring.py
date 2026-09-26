import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from prometheus_client.parser import text_string_to_metric_families

from app.core.config import ROOT, Settings


def metric_total(text: str, name: str, labels: dict[str, str]) -> float:
    return sum(
        sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
        if sample.name == name and all(sample.labels.get(key) == value for key, value in labels.items())
    )


async def exercise_worker(api: httpx.AsyncClient) -> dict:
    credentials = dotenv_values(ROOT / "data/admin-credentials.txt")
    login = await api.post(
        "/v1/admin/auth/login",
        json={"username": credentials["username"], "password": credentials["password"]},
    )
    login.raise_for_status()
    headers = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
    marker = uuid4().hex
    uploaded = await api.post(
        "/v1/admin/documents/upload",
        headers=headers,
        files={
            "file": (
                f"monitoring-{marker}.html",
                f"<html><head><title>监控验收 {marker}</title></head><body><main><h1>Redis 缓存穿透</h1><p>布隆过滤器和短期空值缓存可以减少不存在的数据反复访问数据库。此文档只用于验证真实 Worker 指标采集。</p></main></body></html>".encode(),
                "text/html",
            )
        },
    )
    uploaded.raise_for_status()
    doc_id = uploaded.json()["data"]["doc_id"]
    try:
        async with asyncio.timeout(300):
            while True:
                response = await api.get(f"/v1/admin/documents/{doc_id}", headers=headers)
                response.raise_for_status()
                document = response.json()["data"]
                if document["status"] == "ready":
                    return {"doc_id": doc_id, "status": "ready", "chunks": document["total_chunks"]}
                if document["status"] == "failed":
                    raise RuntimeError("Monitoring fixture ingestion failed; inspect the task in admin UI")
                await asyncio.sleep(1)
    finally:
        deleted = await api.delete(f"/v1/admin/documents/{doc_id}", headers=headers)
        deleted.raise_for_status()
        async with asyncio.timeout(60):
            while True:
                response = await api.get(f"/v1/admin/documents/{doc_id}", headers=headers)
                if response.status_code == 404:
                    break
                response.raise_for_status()
                await asyncio.sleep(1)


async def main(exercise: bool) -> None:
    settings = Settings()
    headers = {"Authorization": "Bearer " + settings.metrics_token.get_secret_value()}
    report: dict = {"created_at": datetime.now(UTC).isoformat()}
    async with (
        httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30) as api,
        httpx.AsyncClient(base_url="http://127.0.0.1:9090", timeout=30) as prometheus,
        httpx.AsyncClient(
            base_url="http://127.0.0.1:3000",
            auth=("admin", (ROOT / "data/monitoring/grafana-password.txt").read_text().strip()),
            timeout=30,
        ) as grafana,
    ):
        assert (await api.get("/metrics")).status_code == 401
        assert (await api.get("/metrics", headers={"Authorization": "Bearer invalid"})).status_code == 401
        metrics = await api.get("/metrics", headers=headers)
        metrics.raise_for_status()
        before = metrics.text
        assert "knowforge_tasks" in before
        report["metrics_auth"] = "passed"
        if exercise:
            report["worker"] = await exercise_worker(api)
            report["worker"]["temporary_document_deleted"] = True
            metrics = await api.get("/metrics", headers=headers)
            metrics.raise_for_status()
            after = metrics.text
            for name, labels in [
                ("knowforge_task_runs_total", {"type": "ingest", "outcome": "succeeded"}),
                (
                    "knowforge_model_requests_total",
                    {"priority": "batch", "operation": "embedding", "outcome": "ok"},
                ),
            ]:
                delta = metric_total(after, name, labels) - metric_total(before, name, labels)
                assert delta >= 1, f"Worker metric not aggregated into API scrape: {name}"
                report["worker"][name] = delta
        async with asyncio.timeout(45):
            while True:
                response = await prometheus.get("/api/v1/targets")
                response.raise_for_status()
                targets = [
                    target
                    for target in response.json()["data"]["activeTargets"]
                    if target["labels"].get("job") == "knowforge"
                ]
                if targets and all(target["health"] == "up" for target in targets):
                    break
                await asyncio.sleep(1)
        report["targets"] = [{"health": target["health"], "url": target["scrapeUrl"]} for target in targets]
        rules = await prometheus.get("/api/v1/rules")
        rules.raise_for_status()
        alerts = [rule for group in rules.json()["data"]["groups"] for rule in group["rules"]]
        assert len(alerts) == 7 and all(rule["health"] == "ok" for rule in alerts)
        report["alerts"] = [{"name": rule["name"], "state": rule["state"]} for rule in alerts]
        datasource = await grafana.get("/api/datasources/uid/knowforge-prometheus/health")
        datasource.raise_for_status()
        assert datasource.json()["status"] == "OK"
        response = await grafana.get("/api/dashboards/uid/knowforge-overview")
        response.raise_for_status()
        dashboard = response.json()["dashboard"]
        assert len(dashboard["panels"]) == 12
        report["dashboard"] = {"uid": dashboard["uid"], "panels": len(dashboard["panels"]), "queries": []}
        for panel in dashboard["panels"]:
            for target in panel["targets"]:
                response = await prometheus.get("/api/v1/query", params={"query": target["expr"]})
                response.raise_for_status()
                result = response.json()
                assert result["status"] == "success", f"Invalid dashboard query in panel {panel['id']}"
                report["dashboard"]["queries"].append(
                    {"panel": panel["id"], "ref": target["refId"], "series": len(result["data"]["result"])}
                )
        if exercise:
            async with asyncio.timeout(45):
                while True:
                    response = await prometheus.get(
                        "/api/v1/query",
                        params={"query": 'sum(knowforge_task_runs_total{type="ingest",outcome="succeeded"})'},
                    )
                    response.raise_for_status()
                    samples = response.json()["data"]["result"]
                    if samples and float(samples[0]["value"][1]) >= 1:
                        report["worker"]["prometheus_received"] = True
                        break
                    await asyncio.sleep(1)
    report["completed_at"] = datetime.now(UTC).isoformat()
    path = ROOT / "data/acceptance/monitoring-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exercise-worker", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    asyncio.run(main(args.exercise_worker))
    print(f"Monitoring acceptance completed in {time.monotonic() - started:.1f}s")
