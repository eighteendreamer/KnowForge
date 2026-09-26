"""Measure per-path capacity with k6 instead of asserting a fictional uniform QPS.

Each scenario runs alone at an escalating arrival rate so the breaking point is attributable:
``cached`` replays a warm query set (no model calls), ``metadata`` exercises the key-free and
category reads, ``cold`` always appends a random suffix so every request misses the cache and pays
the real remote embedding/rerank latency. Search-cache hit rate for each run comes from the
Prometheus counters scraped off the running deployment.
"""

import argparse
import asyncio
import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8000"
CONTAINER_BASE_URL = "http://host.docker.internal:8000"
PROMETHEUS = "http://127.0.0.1:9090/api/v1/query"
BUDGETS = {"cached": 800, "metadata": 800, "cold": 3000, "model": 90_000}
DEFAULT_LADDER = "cached=30,60,120,240;metadata=50,150,300;cold=20,50,100"
# 缓存命中路径的定义就是"不调用模型"，所以预热与压测必须发送同一份去模型化的请求体，否则命中率是假的。
NO_MODEL_OPTIONS = {"rerank": False, "query_rewrite": False}
CACHEABLE_MODES = ["semantic", "keyword", "fuzzy", "hybrid"]
# Prometheus 每 15s 抓取一次；不等它落盘就取计数器差值，上一档（含预热）的流量会混进本档窗口。
SETTLE_SECONDS = 20


def sample_queries(dataset: Path, limit: int) -> list[str]:
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row["query"] for row in rows if not row.get("control")][:limit]


async def login(client: httpx.AsyncClient, credentials: dict) -> str:
    response = await client.post(
        "/v1/admin/auth/login",
        json={"username": credentials["username"], "password": credentials["password"]},
    )
    response.raise_for_status()
    return response.json()["data"]["access_token"]


async def cache_counters() -> dict[str, float]:
    outcome: dict[str, float] = {}
    async with httpx.AsyncClient(timeout=20) as client:
        for name in ("hit", "miss"):
            try:
                response = await client.get(
                    PROMETHEUS, params={"query": f'sum(knowforge_search_cache_total{{outcome="{name}"}})'}
                )
                value = response.json()["data"]["result"][0]["value"][1]
                outcome[name] = float(value)
            except (httpx.HTTPError, KeyError, IndexError, ValueError):
                outcome[name] = 0.0
    return outcome


def parse_ladder(text: str) -> dict[str, list[int]]:
    ladder = {}
    for part in text.split(";"):
        if not part.strip():
            continue
        name, rates = part.split("=")
        ladder[name.strip()] = [int(rate) for rate in rates.split(",")]
    return ladder


def metric(summary: dict, name: str, scenario: str) -> dict:
    metrics = summary.get("metrics", {})
    return metrics.get(f"{name}{{scenario:{scenario}}}") or metrics.get(name) or {}


async def warm_cache(key: str, sample: list[str]) -> dict:
    """Prime every request body the cached scenario can send; a silent miss here invalidates the whole scenario.

    A mode whose warm-up cannot complete (a model-dependent path with no quota, for instance) is left out of
    the rotation instead of being measured as a cache miss, because the cached scenario is by definition the
    path that never reaches a model.
    """
    failures: dict[str, int] = {}
    async with httpx.AsyncClient(base_url=BASE_URL + "/v1", timeout=180) as warm:
        warm.headers["Authorization"] = "Bearer " + key
        for mode in CACHEABLE_MODES:
            failed = 0
            for query in sample:
                response = await warm.post(
                    "/knowledge/search",
                    json={"query": query, "search_type": mode, "top_k": 5, "options": NO_MODEL_OPTIONS},
                )
                failed += response.status_code != 200
            failures[mode] = failed
    primed = [mode for mode, failed in failures.items() if not failed]
    return {
        "requests": len(failures) * len(sample),
        "failed": sum(failures.values()),
        "failed_by_mode": failures,
        "primed_modes": primed,
    }


async def run_k6(
    key: str,
    sample: list[str],
    scenario: str,
    rate: int,
    duration: str,
    vus: int,
    modes: list[str] | None = None,
) -> tuple[int, dict]:
    command = [
        "docker",
        "run",
        "--rm",
        "-e",
        f"KNOFORGE_BASE_URL={CONTAINER_BASE_URL}",
        "-e",
        f"KNOFORGE_API_KEY={key}",
        "-e",
        f"KNOFORGE_QUERIES={json.dumps(sample, ensure_ascii=False)}",
        "-e",
        f"KNOFORGE_SCENARIO={scenario}",
        "-e",
        f"KNOFORGE_RATE={rate}",
        "-e",
        f"KNOFORGE_DURATION={duration}",
        "-e",
        f"KNOFORGE_VUS={vus}",
        *(["-e", f"KNOFORGE_CACHED_MODES={','.join(modes)}"] if modes else []),
        "-v",
        f"{ROOT / 'deploy/load'}:/load",
        "grafana/k6:latest",
        "run",
        "/load/scenarios.js",
    ]
    process = await asyncio.to_thread(
        subprocess.run, command, capture_output=True, text=True, encoding="utf-8", timeout=1800
    )
    stdout = process.stdout
    if "KNOWFORGE_SUMMARY" not in stdout:
        # k6 启动失败也会返回 0，把原因带出去，否则空摘要会被当成"通过"。
        reason = (process.stderr or stdout or "").strip().splitlines()
        return process.returncode, {"_stderr": " | ".join(reason[-4:])[-600:]}
    summary = json.JSONDecoder().raw_decode(stdout.split("KNOWFORGE_SUMMARY", 1)[1])[0]
    return process.returncode, summary


def summarize(summary: dict, scenario: str) -> dict:
    if "_stderr" in summary:
        return {"k6_failure": summary["_stderr"]}
    # k6 把数值放在 metric 的 "values" 里，且 http_req_failed 的比率字段叫 rate 不叫 value；
    # 早先直接在外层取值，四个场景的延迟全部读成 null，再被 `or 0` 当成达标通过。
    duration = metric(summary, "http_req_duration", scenario).get("values", {})
    requests = metric(summary, "http_reqs", scenario).get("values", {})
    failed = metric(summary, "http_req_failed", scenario).get("values", {})
    errors = metric(summary, "knowforge_errors", scenario).get("values", {})
    return {
        "requests": requests.get("count"),
        "failed_rate": failed.get("rate"),
        "application_errors": errors.get("count"),
        "p50_ms": duration.get("med"),
        "p95_ms": duration.get("p(95)"),
        "p99_ms": duration.get("p(99)"),
        "max_ms": duration.get("max"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/evaluation/frozen-2026-09.jsonl")
    parser.add_argument("--queries", type=int, default=40)
    parser.add_argument("--ladder", default=DEFAULT_LADDER)
    parser.add_argument("--duration", default="30s")
    parser.add_argument("--model-vus", type=int, default=3, help="完整 hybrid 链路场景的固定 VU 数")
    parser.add_argument(
        "--with-model",
        action="store_true",
        help="追加消耗远程模型配额的完整 hybrid 链路场景",
    )
    args = parser.parse_args()
    sample = sample_queries(args.dataset, args.queries)
    credentials = dotenv_values(ROOT / "data/admin-credentials.txt")
    runs: list[dict] = []

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as client:
        token = await login(client, credentials)
        headers = {"Authorization": "Bearer " + token}
        created = await client.post(
            "/v1/admin/api-keys",
            headers=headers,
            json={
                "name": f"load {time.time_ns()}",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                # 压测 Key 的限流必须高于被测速率，否则"首个失败速率"量到的是这把 Key 的配额而不是节点容量。
                "rate_limit_per_minute": 100_000,
                "rate_limit_per_day": 2_000_000,
            },
        )
        created.raise_for_status()
        key = created.json()["data"]
    try:
        warm_report = await warm_cache(key["key"], sample)
        ladder = parse_ladder(args.ladder)
        for scenario, rates in ladder.items():
            for rate in rates:
                if scenario == "cached" and rate != rates[0]:
                    # 缓存条目会过期（当前 240s），阶梯里每一档都要重新灌满，否则测的是 miss 而不是 hit。
                    warm_report = await warm_cache(key["key"], sample)
                modes = warm_report["primed_modes"] if scenario == "cached" else None
                if scenario == "cached" and not modes:
                    runs.append(
                        {
                            "scenario": "cached",
                            "target_rate": rate,
                            "duration": args.duration,
                            "thresholds_passed": False,
                            "skipped": True,
                            "skip_reason": "没有任何模式完成缓存预热，命中路径无法在不碰模型的前提下测量",
                            "warm_up": warm_report,
                        }
                    )
                    print(json.dumps(runs[-1], ensure_ascii=False), flush=True)
                    continue
                await asyncio.sleep(SETTLE_SECONDS)
                before = await cache_counters()
                code, summary = await run_k6(
                    key["key"], sample, scenario, rate, args.duration, args.model_vus, modes
                )
                await asyncio.sleep(SETTLE_SECONDS)
                after = await cache_counters()
                stats = summarize(summary, scenario)
                hits = after["hit"] - before["hit"]
                misses = after["miss"] - before["miss"]
                passed = (
                    code == 0
                    # 摘要没解析出来时 stats 里全是 None，`or 0` 会让空跑变成"通过"，必须先确认测到了延迟分布。
                    and stats.get("p95_ms") is not None
                    and (stats["failed_rate"] or 0) < 0.01
                    and stats["p95_ms"] <= BUDGETS[scenario]
                )
                runs.append(
                    {
                        "scenario": scenario,
                        "target_rate": rate,
                        "duration": args.duration,
                        "thresholds_passed": passed,
                        "exit_code": code,
                        "cache_hits": round(hits),
                        "cache_misses": round(misses),
                        "cache_hit_rate": round(hits / (hits + misses), 4) if hits + misses else None,
                        **stats,
                    }
                )
                print(json.dumps(runs[-1], ensure_ascii=False), flush=True)
        if args.with_model:
            model_code, model_summary = await run_k6(
                key["key"], sample, "model", args.model_vus, args.duration, args.model_vus
            )
            runs.append(
                {
                    "scenario": "model",
                    "target_rate": None,
                    "vus": args.model_vus,
                    "duration": args.duration,
                    "thresholds_passed": model_code == 0,
                    "exit_code": model_code,
                    **summarize(model_summary, "model"),
                }
            )
            print(json.dumps(runs[-1], ensure_ascii=False), flush=True)
        else:
            runs.append(
                {
                    "scenario": "model",
                    "target_rate": None,
                    "duration": args.duration,
                    "thresholds_passed": False,
                    "skipped": True,
                    "skip_reason": "完整 hybrid 链路要消耗远程模型配额，需要显式 --with-model 才发起",
                }
            )
    finally:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as client:
            token = await login(client, credentials)
            revoked = await client.delete(
                f"/v1/admin/api-keys/{key['id']}", headers={"Authorization": "Bearer " + token}
            )

    capacity = {}
    for scenario in ("cached", "metadata", "cold", "model"):
        measured = [run for run in runs if run["scenario"] == scenario and not run.get("skipped")]
        accepted = [run for run in measured if run["thresholds_passed"]]
        breaking = [run for run in measured if not run["thresholds_passed"]]
        capacity[scenario] = {
            "sustained_rate": max((run["target_rate"] or run.get("vus", 0)) for run in accepted)
            if accepted
            else 0,
            "first_failing_rate": min((run["target_rate"] or 0) for run in breaking) if breaking else None,
            "p95_budget_ms": BUDGETS[scenario],
            "measured": bool(measured),
        }
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "scope": (
            "单节点本地部署（API + PostgreSQL + Redis + Qdrant 同机）；"
            "cached/metadata/cold 三条路径不发起远程模型调用，完整 hybrid 链路仅在 --with-model 下测量"
        ),
        "key_revoked": revoked.json().get("data", {}).get("status"),
        "load_key_limits": {
            "rate_limit_per_minute": key.get("rate_limit_per_minute"),
            "rate_limit_per_day": key.get("rate_limit_per_day"),
        },
        "queries_in_rotation": len(sample),
        "cache_warm_up": warm_report,
        "runs": runs,
        "capacity": capacity,
        "slo_statement": (
            "缓存命中路径与元数据接口按实测速率与 p95 承诺；包含远程模型调用的冷查询单独报告延迟分布，"
            "不与缓存路径共用同一 QPS/P99 承诺。"
        ),
    }
    path = ROOT / "data/acceptance/load-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "capacity": capacity}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
