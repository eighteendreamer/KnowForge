"""Score the active index on a frozen or tuning query set through the real HTTP search API."""

import argparse
import asyncio
import hashlib
import json
import math
import re
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from itertools import combinations
from pathlib import Path
from uuid import UUID

import httpx
from dotenv import dotenv_values

from app.core.config import Settings
from app.core.database import make_session_factory
from app.core.errors import AppError
from app.schemas.system import EVALUATION_TARGETS
from app.services.runtime_config import build_settings

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8000"


def search_options(query_rewrite: bool, rerank: bool) -> dict:
    """方案 8.1.4：指标以 Chunk 为单位、在相邻 Chunk 合并展示之前计算，所以评估一律关掉合并。"""
    return {"query_rewrite": query_rewrite, "rerank": rerank, "merge_adjacent": False}


VARIANTS = [
    search_options(True, True),
    search_options(False, True),
    search_options(True, False),
    search_options(False, False),
]
PRODUCTION = VARIANTS[0]
VARIANT_NAMES = {
    "production": PRODUCTION,
    "no_rewrite": search_options(False, True),
    "no_rerank": search_options(True, False),
    "literal": search_options(False, False),
}


def select_variants(names: list[str] | None, ablation: bool) -> list[dict]:
    """默认矩阵是"生产变体 +（可选）改写/精排开关对照"。显式点名变体才能在模型不可用时只跑字面路径。"""
    if names:
        unknown = [name for name in names if name not in VARIANT_NAMES]
        if unknown:
            raise SystemExit(f"未知的 --variants 取值：{unknown}，可选 {sorted(VARIANT_NAMES)}")
        return [VARIANT_NAMES[name] for name in dict.fromkeys(names)]
    return VARIANTS if ablation else [PRODUCTION]


def dcg(grades: list[int]) -> float:
    return sum(grade / math.log2(index + 2) for index, grade in enumerate(grades))


def score_query(retrieved: list[str], labels: dict[str, int]) -> dict:
    relevant = {chunk for chunk, grade in labels.items() if grade > 0}
    grades = [labels.get(chunk, 0) for chunk in retrieved]
    best = sorted(labels.values(), reverse=True)
    ideal_relevant = sorted(relevant, key=lambda chunk: -labels[chunk])
    top5, top10 = retrieved[:5], retrieved[:10]
    hits_in_top5 = [chunk for chunk in top5 if chunk in relevant]
    first = next((index + 1 for index, chunk in enumerate(retrieved) if chunk in relevant), None)
    full = sum(1 for chunk in relevant if chunk not in set(top5))
    return {
        "recall_at_5": len(hits_in_top5) / len(relevant) if relevant else None,
        "recall_at_10": (
            sum(1 for chunk in top10 if chunk in relevant) / len(relevant) if relevant else None
        ),
        "missed_at_5": full,
        "reciprocal_rank": (1 / first) if first else 0.0,
        "ndcg_at_10": (dcg(grades[:10]) / dcg(best[:10])) if best and dcg(best[:10]) else None,
        "precision_at_5": len(hits_in_top5) / 5,
        "hit_at_5": 1 if hits_in_top5 else 0,
        "relevant_order": ideal_relevant,
        "false_positive_top5": sum(1 for chunk in top5 if chunk not in relevant),
    }


def load_dataset(dataset: Path) -> tuple[list[dict], str]:
    body = dataset.read_bytes()
    rows = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]
    return rows, hashlib.sha256(body).hexdigest()


def gate_variant(variants: list[dict]) -> dict:
    """Pick the variant that gates a switch.

    Only the configured production mode exercises the dense vectors, so recording the best-scoring variant
    would let a weaker embedding model pass on BM25 alone.
    """
    candidates = [item for item in variants if item["mode"] == "hybrid" and item["options"] == PRODUCTION]
    if not candidates:
        raise SystemExit(
            "缺少混合检索 + 查询改写 + 精排的评估结果，无法作为切换门；请按默认矩阵跑满 --modes 与 --variants"
        )
    return candidates[0]


async def search_once(api: httpx.AsyncClient, mode: str, options: dict, query: str) -> httpx.Response:
    response = None
    for attempt in range(6):
        response = await api.post(
            "/knowledge/search",
            json={"query": query, "search_type": mode, "top_k": 10, "options": options},
        )
        # 429 and 5xx are environmental, not retrieval failures: retrying keeps them out of the metrics.
        if response.status_code < 500 and response.status_code != 429 or attempt == 5:
            return response
        retry_after = float(response.headers.get("Retry-After", "5"))
        await asyncio.sleep(min(retry_after, 60) + 2 * (attempt + 1))
    return response  # pragma: no cover


class DirectRunner:
    """Evaluate a configuration version through the same retrieval chain, without HTTP."""

    def __init__(self, configuration_id: str) -> None:
        from qdrant_client import AsyncQdrantClient
        from redis.asyncio import Redis

        from app.core.database import make_engine
        from app.core.model_client import ModelGateway
        from app.services.storage.qdrant_store import QdrantStore

        self.configuration_id = configuration_id
        self.defaults = Settings()
        self.engine = make_engine(self.defaults)
        self.sessions = make_session_factory(self.engine)
        self.redis = Redis.from_url(self.defaults.redis_url, decode_responses=True)
        self.qdrant = AsyncQdrantClient(
            url=self.defaults.qdrant_url,
            api_key=self.defaults.qdrant_api_key.get_secret_value() or None,
        )
        self.model_factory = ModelGateway
        self.store_factory = QdrantStore

    async def settings(self) -> object:
        from app.models import RuntimeConfiguration

        async with self.sessions() as session:
            row = await session.get(RuntimeConfiguration, UUID(self.configuration_id))
        if row is None:
            raise SystemExit("配置版本不存在")
        return build_settings(self.defaults, row.values, row.index_fingerprint, row.id)

    async def close(self) -> None:
        await self.qdrant.close()
        await self.redis.aclose()
        await self.engine.dispose()

    async def search(self, body: dict, semaphore: asyncio.Semaphore) -> dict:
        from app.schemas.search import SearchInput
        from app.services.retrieval.search_service import search

        settings = await self.settings()
        for attempt in range(6):
            try:
                async with self.sessions() as session:
                    async with semaphore:
                        return await search(
                            session,
                            SearchInput(**body),
                            settings,
                            self.model_factory(settings, self.redis),
                            self.store_factory(self.qdrant, settings),
                            self.redis,
                            public_only=True,
                        )
            except AppError as exc:
                if exc.code != 3001 or attempt == 5:
                    raise
                await asyncio.sleep(min(float((exc.headers or {}).get("Retry-After", "5")), 60) + 2)
        raise AssertionError("unreachable")


async def run(
    rows: list[dict],
    dataset_info: dict,
    modes: list[str],
    label: str,
    variants: list[dict],
    parallel: int,
    direct: str | None = None,
) -> dict:
    credentials = dotenv_values(ROOT / "data/admin-credentials.txt")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "dataset": dataset_info,
        "label": label,
        "variants": [],
    }
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=180) as client:
        login = await client.post(
            "/v1/admin/auth/login",
            json={"username": credentials["username"], "password": credentials["password"]},
        )
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
        created = await client.post(
            "/v1/admin/api-keys",
            headers=headers,
            json={
                "name": f"evaluation {label} {time.time_ns()}",
                # The revoke below cannot run if the process is killed, so the key must expire by itself.
                "expires_at": (datetime.now(UTC) + timedelta(hours=6)).isoformat(),
                "rate_limit_per_minute": 600,
                # A full four-mode sweep is thousands of requests; the 1000/day key default would stall it.
                "rate_limit_per_day": 200_000,
            },
        )
        created.raise_for_status()
        key = created.json()["data"]
        api = httpx.AsyncClient(
            base_url=BASE_URL + "/v1", timeout=180, headers={"Authorization": "Bearer " + key["key"]}
        )
        latencies: dict[str, list[float]] = defaultdict(list)
        runner = DirectRunner(direct) if direct else None
        try:
            for mode in modes:
                for options in variants:
                    per_query = []
                    controls = []
                    started = time.perf_counter()
                    for start in range(0, len(rows), parallel):
                        batch = rows[start : start + parallel]
                        if direct:
                            semaphore = asyncio.Semaphore(parallel)
                            results = await asyncio.gather(
                                *(
                                    runner.search(
                                        {
                                            "query": row["query"],
                                            "search_type": mode,
                                            "top_k": 10,
                                            "options": options,
                                        },
                                        semaphore,
                                    )
                                    for row in batch
                                )
                            )
                            for row, data in zip(batch, results, strict=True):
                                retrieved = [item["id"] for item in data["results"]]
                                latencies[f"{mode}|{json.dumps(options, sort_keys=True)}"].append(
                                    data["took_ms"]
                                )
                                scored = score_query(retrieved, row["labels"])
                                (controls if row.get("control") else per_query).append(scored)
                            continue
                        responses = await asyncio.gather(
                            *(search_once(api, mode, options, row["query"]) for row in batch)
                        )
                        for row, response in zip(batch, responses, strict=True):
                            response.raise_for_status()
                            data = response.json()["data"]
                            retrieved = [item["id"] for item in data["results"]]
                            latencies[f"{mode}|{json.dumps(options, sort_keys=True)}"].append(data["took_ms"])
                            scored = score_query(retrieved, row["labels"])
                            if row.get("control"):
                                controls.append(scored)
                            else:
                                per_query.append(scored)

                    def average(key: str, rows: list[dict]) -> float:
                        values = [row[key] for row in rows if row[key] is not None]
                        return round(statistics.fmean(values), 4) if values else 0.0

                    variant = {
                        "mode": mode,
                        "options": options,
                        "scored_queries": len(per_query),
                        "recall_at_5": average("recall_at_5", per_query),
                        "recall_at_10": average("recall_at_10", per_query),
                        "mrr": round(statistics.fmean(row["reciprocal_rank"] for row in per_query), 4),
                        "ndcg_at_10": average("ndcg_at_10", per_query),
                        "precision_at_5": average("precision_at_5", per_query),
                        "hit_at_5": round(statistics.fmean(row["hit_at_5"] for row in per_query), 4),
                        "missed_relevant_at_5": sum(row["missed_at_5"] for row in per_query),
                        "control_queries": len(controls),
                        "control_false_positives_top5": sum(row["false_positive_top5"] for row in controls),
                        "latency_ms_p95": round(
                            sorted(latencies[f"{mode}|{json.dumps(options, sort_keys=True)}"])[
                                int(len(latencies[f"{mode}|{json.dumps(options, sort_keys=True)}"]) * 0.95)
                                - 1
                            ]
                        ),
                        "wall_seconds": round(time.perf_counter() - started, 1),
                    }
                    variant["targets_passed"] = all(
                        variant[key] >= value for key, value in EVALUATION_TARGETS.items()
                    )
                    report["variants"].append(variant)
                    print(json.dumps(variant, ensure_ascii=False), flush=True)
        finally:
            await api.aclose()
            if runner is not None:
                await runner.close()
            await client.delete("/v1/admin/api-keys/" + str(key["id"]), headers=headers)
    best = max(report["variants"], key=lambda item: (item["recall_at_10"], item["ndcg_at_10"]))
    report["best_variant"] = {
        "mode": best["mode"],
        "options": best["options"],
        "recall_at_10": best["recall_at_10"],
    }
    # 门指标只在测到了"混合检索 + 改写 + 精排"这一格时才算得出来；只跑字面路径的离线评估不该被这件事卡死。
    production_measured = any(
        item["mode"] == "hybrid" and item["options"] == PRODUCTION for item in report["variants"]
    )
    gate = gate_variant(report["variants"]) if production_measured else None
    report["headline"] = (
        None
        if gate is None
        else {
            "mode": gate["mode"],
            "options": gate["options"],
            "recall_at_5": gate["recall_at_5"],
            "recall_at_10": gate["recall_at_10"],
            "mrr": gate["mrr"],
            "ndcg_at_10": gate["ndcg_at_10"],
            "precision_at_5": gate["precision_at_5"],
            "targets_passed": gate["targets_passed"],
        }
    )
    report["mode_pairs_compared"] = [list(pair) for pair in combinations(modes, 2)]
    return report


def resolve_direct(direct: str | None, rebuilds: list[dict]) -> str | None:
    """`--direct auto` reads the open rebuild's target so no run needs a hand-copied configuration id."""
    if direct != "auto":
        return direct
    open_rows = [row for row in rebuilds if row["status"] in {"pending", "running", "evaluating", "ready"}]
    if not open_rows:
        raise SystemExit("--direct auto 找不到进行中的重建任务，请显式给出目标配置版本 id")
    return open_rows[0]["target_configuration_id"]


def measured_identity(active: dict, measured: dict) -> dict:
    """A measurement that belongs to neither the online index nor a rebuild target is refused, never just labelled."""
    if measured["configuration_id"] == active["configuration_id"]:
        return measured
    targets = {row["target_configuration_id"]: row["target_collection"] for row in active["rebuilds"]}
    collection = measured["values"]["qdrant_collection"]
    if targets.get(measured["configuration_id"]) != collection:
        raise SystemExit(
            f"评估的配置版本 {measured['configuration_id']} 既不是在线索引也不是任何重建目标"
            f"（其 Collection 为 {collection}），结果无法归属，拒绝落盘"
        )
    return measured


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/evaluation/frozen-2026-09.jsonl")
    parser.add_argument("--modes", nargs="+", default=["semantic", "keyword", "fuzzy", "hybrid"])
    parser.add_argument("--label", default="active")
    parser.add_argument("--dataset-kind", choices=["tuning", "frozen"], default="frozen")
    parser.add_argument("--record", action="store_true", help="POST the headline metrics to the admin API")
    parser.add_argument("--limit", type=int, default=0, help="Stratified subsample of the dataset")
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="也跑 query-rewrite 与 rerank 的开关对照矩阵",
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=sorted(VARIANT_NAMES),
        default=None,
        help="只跑点名的开关组合；模型配额不可用时可用 --variants literal 只跑字面路径",
    )
    parser.add_argument("--parallel", type=int, default=6)
    parser.add_argument(
        "--direct",
        default=None,
        help="评估指定配置版本（走进程内检索链路）；填 auto 则自动取当前未完成重建的目标配置",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.label):
        raise SystemExit(f"--label 只能用于文件名的字符组成，收到 {args.label!r}")
    path = ROOT / args.dataset
    rows, digest = load_dataset(path)
    if args.limit and args.limit < len(rows):
        picked: dict[str, list[dict]] = {}
        for row in rows:
            picked.setdefault(row["category"], []).append(row)
        stride = max(1, len(rows) // args.limit)
        rows = [row for group in picked.values() for row in group[::stride]][: args.limit]
    variants = select_variants(args.variants, args.ablation)
    dataset_info = {
        "path": args.dataset,
        "version": path.stem,
        "sha256": digest,
        "queries": len(rows),
        "full_set_queries": len(load_dataset(path)[0]),
    }
    credentials = dotenv_values(ROOT / "data/admin-credentials.txt")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as client:
        login = await client.post(
            "/v1/admin/auth/login",
            json={"username": credentials["username"], "password": credentials["password"]},
        )
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
        active = (await client.get("/v1/admin/system/settings", headers=headers)).json()["data"]
        direct = resolve_direct(args.direct, active["rebuilds"])
        if direct is None:
            measured = active
        else:
            history = await client.get(f"/v1/admin/system/settings/history/{direct}", headers=headers)
            if history.status_code != 200:
                raise SystemExit(f"配置版本 {direct} 读取失败：HTTP {history.status_code}")
            measured = history.json()["data"]
        measured = measured_identity(active, measured)
    report = await run(rows, dataset_info, args.modes, args.label, variants, max(1, args.parallel), direct)
    report["configuration_id"] = measured["configuration_id"]
    report["index_fingerprint"] = measured["index_fingerprint"]
    report["collection"] = measured["values"]["qdrant_collection"]
    report["embedding_model"] = measured["values"]["embedding_model"]
    output = ROOT / "data/acceptance" / f"evaluation-{args.label}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.record:
        # 记录就是给成对切换当门，缺生产变体时必须拒绝，而不是把字面路径的分数写进去。
        report["headline"] = report["headline"] or gate_variant(report["variants"])
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as client:
            login = await client.post(
                "/v1/admin/auth/login",
                json={"username": credentials["username"], "password": credentials["password"]},
            )
            login.raise_for_status()
            headers = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
            response = await client.post(
                "/v1/admin/system/evaluations",
                headers=headers,
                json={
                    "name": f"{args.label} {report['dataset']['version']}",
                    "dataset_version": report["dataset"]["version"],
                    "dataset_kind": args.dataset_kind,
                    "dataset_hash": report["dataset"]["sha256"],
                    "configuration_id": report["configuration_id"],
                    "metrics": {
                        key: report["headline"][key]
                        for key in ("recall_at_5", "recall_at_10", "mrr", "ndcg_at_10", "precision_at_5")
                    },
                    "notes": json.dumps(report["headline"], ensure_ascii=False),
                },
            )
            response.raise_for_status()
            print(
                json.dumps(
                    {
                        "recorded_evaluation_run": response.json()["data"]["id"],
                        "collection": report["collection"],
                        "embedding_model": report["embedding_model"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    print(
        json.dumps({"headline": report["headline"], "variants": len(report["variants"])}, ensure_ascii=False)
    )


if __name__ == "__main__":
    asyncio.run(main())
