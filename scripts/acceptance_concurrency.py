"""Measure whether slow retrieval routes starve unrelated endpoints.

Start the API with a deliberately small pool so the effect is visible::

    KNOFORGE_DB_POOL_SIZE=1 KNOFORGE_DB_POOL_TIMEOUT=5 uvicorn ... --port 8010

Then run ``uv run python scripts/acceptance_concurrency.py --base-url http://127.0.0.1:8010``.
Each search hits the real embedding, rerank and query-rewrite models, so the probes show what
an operator sees while retrieval is busy.
"""

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
QUERIES = [
    "Redis 缓存穿透如何使用布隆过滤器拦截",
    "PostgreSQL 索引失效的常见场景有哪些",
    "Kafka 分区重平衡会带来哪些影响",
    "向量检索的召回率和精度如何权衡",
    "混合检索里 RRF 融合怎么排序",
]
PROBES = {
    "documents": "/v1/admin/documents",
    "tags": "/v1/admin/tags",
    "categories": "/v1/admin/categories",
    "tasks": "/v1/admin/tasks",
    "api-docs": "/v1/knowledge/api-docs",
}


async def run_search(client: httpx.AsyncClient, query: str) -> dict:
    started = time.perf_counter()
    try:
        response = await client.post(
            "/v1/admin/search",
            json={
                "query": query,
                "search_type": "hybrid",
                "top_k": 8,
                "options": {
                    "highlight": True,
                    "include_metadata": True,
                    "rerank": True,
                    "query_rewrite": True,
                },
            },
        )
        return {
            "query": query,
            "status": response.status_code,
            "seconds": round(time.perf_counter() - started, 2),
        }
    except Exception as error:  # noqa: BLE001 - the point is to report whatever went wrong
        return {
            "query": query,
            "error": type(error).__name__,
            "seconds": round(time.perf_counter() - started, 2),
        }


async def probe(client: httpx.AsyncClient, name: str, url: str, rounds: int) -> dict:
    latencies, failures = [], 0
    for _ in range(rounds):
        started = time.perf_counter()
        try:
            response = await client.get(url, params={"limit": 1})
            if response.status_code >= 400:
                failures += 1
        except Exception:  # noqa: BLE001 - timeouts and connection errors are the finding
            failures += 1
        latencies.append(time.perf_counter() - started)
        await asyncio.sleep(0.2)
    return {
        "name": name,
        "rounds": rounds,
        "failures": failures,
        "p50_ms": round(statistics.median(latencies) * 1000),
        "max_ms": round(max(latencies) * 1000),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--searches", type=int, default=4)
    parser.add_argument("--probe-rounds", type=int, default=25)
    parser.add_argument("--probe-budget-ms", type=int, default=2000)
    parser.add_argument("--output", type=Path, default=ROOT / "data/acceptance/concurrency-report.json")
    args = parser.parse_args()

    credentials = dotenv_values(ROOT / "data/admin-credentials.txt")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=300) as client:
        login = await client.post(
            "/v1/admin/auth/login",
            json={"username": credentials["username"], "password": credentials["password"]},
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["data"]["access_token"]

        searches = [asyncio.create_task(run_search(client, query)) for query in QUERIES[: args.searches]]
        await asyncio.sleep(3)
        probes = await asyncio.gather(
            *(probe(client, name, url, args.probe_rounds) for name, url in PROBES.items())
        )
        results = await asyncio.gather(*searches)

    worst = max(item["max_ms"] for item in probes)
    failed = sum(item["failures"] for item in probes)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "note": "probes ran against the API while real hybrid searches were in flight",
        "searches": results,
        "probes": probes,
        "passed": failed == 0 and worst <= args.probe_budget_ms,
        "worst_probe_ms": worst,
        "probe_failures": failed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in probes:
        print(
            f"{item['name']:<12} p50={item['p50_ms']:>6}ms max={item['max_ms']:>7}ms failures={item['failures']}"
        )
    print("searches:", [(item["query"][:12], item.get("status"), item["seconds"]) for item in results])
    print("passed:", report["passed"], "| worst probe:", worst, "ms")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
