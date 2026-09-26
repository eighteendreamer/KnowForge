"""Build stratified tuning and frozen query sets with pooled graded chunk labels from the real corpus.

Stage 1 enumerates candidate queries from question-formatted section headings and records which
content kinds (table, code, image, cross-page, HTML) each query comes from.
Stage 2 deep-pools the three retrievers per query, then grades every pooled chunk 0/1/2 against
GRADE_RUBRIC with the configured LLM at temperature 0, so Precision@5 is measured over a judged
pool instead of assuming every unjudged chunk is irrelevant (TREC-style pooled judging).
Stage 3 selects a stratified frozen set of the requested size and writes a review CSV; grades in
overrides-<version>.csv replace model grades and the count is recorded.

Pooling and grading are cached in data/evaluation/pool-<version>.jsonl and grades-<version>.jsonl,
so an interrupted run resumes without re-billing model calls.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import random
import re
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from dotenv import dotenv_values
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.cache import revision
from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.core.errors import AppError
from app.core.model_client import ModelGateway
from app.models import Category, Chunk, Document
from app.services.retrieval.fuzzy_search import fuzzy_search

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8000"
QUESTION = re.compile(r"[?？]")
INTERROGATIVE = re.compile(
    r"(什么是|如何|怎么|为什么|有哪些|有哪几|哪些场景|区别|原理|流程|机制|优势|缺点|是否|怎样|注意事项|怎么办)"
)
MAX_QUERY_CHARS = 200
MODES = ("semantic", "keyword", "fuzzy")
POOL_SIZE = 25
CANDIDATE_CHARS = 900
GRADE_BATCH = 6
CONTROL_TOPICS = [
    "打印机脱机队列清理",
    "烘焙面团水合比例",
    "汽车轮胎动平衡参数",
    "钢琴踏板上延音阻尼",
    "候鸟迁徙导航地磁偏角",
]
GRADE_RUBRIC = """你在为中文技术知识库的检索评估集做相关性标注。规则：
2 = 该段落正文直接回答了问题，读者只读这一段就能得到答案要点。
1 = 该段落只覆盖问题的一部分，或提供必需的背景、前提、对比信息。
0 = 主题不同，或仅词面重合但没有实质帮助。
只看段落正文判断，不要因为标题相似就给分。拿不准就给低分。
输入是 JSON：{"q": 问题, "c": [{"i": 序号, "t": 段落正文}]}。
只输出 JSON：{"grades": [{"i": 序号, "g": 0 或 1 或 2}]}，c 里每个序号都要出现一次，不要输出别的字段。"""


def query_origin(text: str) -> str | None:
    """Classify a section heading as an answer-seeking query, or reject it."""
    head = text.split("\n")[0].strip()
    if not 8 <= len(head) <= MAX_QUERY_CHARS:
        return None
    if QUESTION.search(head):
        return "question_heading"
    if INTERROGATIVE.search(head):
        return "interrogative_heading"
    return None


def query_source(chunk: Chunk) -> tuple[str, str] | None:
    """Nearest question-formatted heading on the section path, leaf first.

    An answer that sits under a sub-heading (“Redis 缓存穿透 > 解决方案”) still answers the parent
    question, so walking up widens coverage. Acceptance does not loosen: the pooled judge still has to
    grade the seed chunk as a direct answer of the query that is finally used.
    """
    for depth, element in enumerate(reversed(list(chunk.section_path))):
        origin = query_origin(element)
        if origin:
            return element.strip(), origin if depth == 0 else f"ancestor_{origin}"
    return None


def content_kinds_of(chunk: Chunk, document: Document) -> list[str]:
    kinds = []
    if "| ---" in chunk.text or re.search(r"^\|.+\|$", chunk.text, re.MULTILINE):
        kinds.append("表格")
    if "```" in chunk.text:
        kinds.append("代码")
    if re.search(r"!\[[^\]]*\]\(", chunk.text):
        kinds.append("图片")
    if chunk.page_start is not None and chunk.page_end is not None and chunk.page_end > chunk.page_start:
        kinds.append("跨页")
    if document.file_type == "html":
        kinds.append("HTML")
    return sorted(kinds) or ["纯文本"]


def split_documents(documents: list[Document]) -> tuple[set[int], set[int]]:
    """Assign each document to exactly one set so tuning queries never leak into the frozen set."""
    tuning, frozen = set(), set()
    for document in documents:
        digest = hashlib.sha256(document.doc_id.encode()).digest()
        (tuning if digest[0] % 5 == 0 else frozen).add(document.id)
    return tuning, frozen


def row_key(row: dict, corpus: str) -> str:
    """Keyed by the knowledge-base revision so pools graded against one corpus are never reused for another."""
    return hashlib.sha256(
        f"{corpus}|{row['doc_id']}|{row['query']}|{row['seed_chunk_id']}".encode()
    ).hexdigest()[:24]


async def enumerate_queries(session, documents: list[Document]) -> dict[str, list[dict]]:
    categories = {row.id: row.path for row in await session.scalars(select(Category))}
    tuning_ids, frozen_ids = split_documents(documents)
    buckets: dict[str, list[dict]] = {"tuning": [], "frozen": []}
    for document in documents:
        chunks = list(
            await session.scalars(
                select(Chunk).where(Chunk.doc_id == document.id).order_by(Chunk.chunk_index)
            )
        )
        category = categories.get(document.manual_category_id or document.auto_category_id or 0, "未分类")
        for chunk in chunks:
            source = query_source(chunk)
            if source is None:
                continue
            heading, origin = source
            buckets["tuning" if document.id in tuning_ids else "frozen"].append(
                {
                    "query": heading[:MAX_QUERY_CHARS],
                    "query_origin": origin,
                    "doc_id": document.doc_id,
                    "source": document.original_filename,
                    "file_type": document.file_type,
                    "category": category,
                    "difficulty": chunk.difficulty,
                    "content_kinds": content_kinds_of(chunk, document),
                    "seed_chunk_id": chunk.chunk_id,
                    "labels": {},
                }
            )
    return buckets


def read_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {
        row["key"]: row
        for row in (
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    }


async def collect_pools(
    rows: list[dict], key: str, cache_path: Path, parallel: int, corpus: str
) -> dict[str, list[str]]:
    """Union the three retrievers per query; cached so a rerun only pools the missing rows."""
    cache = read_cache(cache_path)
    todo = [row for row in rows if row_key(row, corpus) not in cache]
    semaphore = asyncio.Semaphore(parallel)

    async def one(client: httpx.AsyncClient, row: dict) -> None:
        pooled: list[str] = []
        async with semaphore:
            for mode in MODES:
                attempts = 0
                while True:
                    attempts += 1
                    response = await client.post(
                        "/knowledge/search",
                        json={
                            "query": row["query"],
                            "search_type": mode,
                            "top_k": POOL_SIZE,
                            "options": {
                                "highlight": False,
                                "include_metadata": False,
                                "rerank": False,
                                "query_rewrite": False,
                            },
                        },
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        # The remote embedding model intermittently returns 503 under load; a multi-hour
                        # offline build must not die on one such response. A daily-quota 429 answers with
                        # Retry-After = seconds until midnight, so this wait must stay capped.
                        if attempts < 6:
                            await asyncio.sleep(
                                min(float(response.headers.get("Retry-After", "10")), 60) + 3 * attempts
                            )
                            continue
                        raise RuntimeError(f"检索未恢复：{row['query'][:30]} HTTP {response.status_code}")
                    response.raise_for_status()
                    pooled.extend(item["id"] for item in response.json()["data"]["results"])
                    break
        entry = {"key": row_key(row, corpus), "pool": list(dict.fromkeys(pooled))}
        cache[entry["key"]] = entry
        with cache_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    async with httpx.AsyncClient(
        base_url=BASE_URL + "/v1", timeout=180, headers={"Authorization": "Bearer " + key}
    ) as client:
        for start in range(0, len(todo), parallel):
            await asyncio.gather(*(one(client, row) for row in todo[start : start + parallel]))
            print(json.dumps({"pooled": min(start + parallel, len(todo)), "of": len(todo)}), flush=True)
    return {row_key(row, corpus): cache[row_key(row, corpus)]["pool"] for row in rows}


async def grade_pools(
    rows: list[dict],
    pools: dict[str, list[str]],
    texts: dict[str, str],
    cache_path: Path,
    parallel: int,
    corpus: str,
) -> tuple[dict[str, dict[str, int]], int, int]:
    """Grade every pooled candidate; returns labels, failed query count and unjudged candidate count."""
    settings = Settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    gateway = ModelGateway(settings, redis)
    cache = read_cache(cache_path)
    semaphore = asyncio.Semaphore(parallel)

    async def ask_once(query: str, batch: list[str]) -> dict[int, int]:
        content = json.dumps(
            {
                "q": query,
                "c": [
                    {"i": index, "t": texts.get(chunk_id, "")[:CANDIDATE_CHARS]}
                    for index, chunk_id in enumerate(batch)
                ],
            },
            ensure_ascii=False,
        )
        async with semaphore:
            for attempt in range(4):
                try:
                    result = await gateway.json_chat(GRADE_RUBRIC, content, "batch")
                    return {
                        int(item["i"]): int(item["g"])
                        for item in result["grades"]
                        if isinstance(item, dict) and str(item.get("i", "")).isdigit()
                    }
                except AppError as error:
                    # A 3001 means the shared model bucket is full: it is a wait, not a lost query.
                    if error.code != 3001 or attempt == 3:
                        raise
                    await asyncio.sleep(min(float((error.headers or {}).get("Retry-After", "2")) + 1, 60))
                except Exception as error:  # noqa: BLE001 - retry any malformed model answer
                    if attempt == 3:
                        raise RuntimeError(f"标注失败：{query[:30]} {type(error).__name__}") from error
                    await asyncio.sleep(min(5 * (attempt + 1), 60))
            return {}

    async def grade_batch(query: str, ids: list[str]) -> tuple[dict[str, int], list[str]]:
        """The model sometimes answers fewer items than asked, so re-ask only the missing ones."""
        grades: dict[str, int] = {}
        pending = list(ids)
        for _ in range(3):
            if not pending:
                break
            missing: list[str] = []
            for start in range(0, len(pending), GRADE_BATCH):
                batch = pending[start : start + GRADE_BATCH]
                returned = await ask_once(query, batch)
                for index, chunk_id in enumerate(batch):
                    if index in returned:
                        grades[chunk_id] = returned[index]
                    else:
                        missing.append(chunk_id)
            pending = missing
        judged = {chunk_id: grade for chunk_id, grade in grades.items() if grade in (0, 1, 2)}
        return judged, [chunk_id for chunk_id in ids if chunk_id not in judged]

    todo = [row for row in rows if row_key(row, corpus) not in cache and pools.get(row_key(row, corpus))]
    failed: list[str] = []
    unjudged_total = 0
    for start in range(0, len(todo), parallel):
        batch = todo[start : start + parallel]
        # One unreachable model answer must not throw away an hour of grading: isolate per query, and let
        # the next run retry only the queries that never made it into the cache.
        graded = await asyncio.gather(
            *(grade_batch(row["query"], pools[row_key(row, corpus)]) for row in batch),
            return_exceptions=True,
        )
        with cache_path.open("a", encoding="utf-8") as handle:
            for row, outcome in zip(batch, graded, strict=True):
                if isinstance(outcome, BaseException):
                    failed.append(f"{type(outcome).__name__}: {row['query'][:40]}")
                    continue
                judged, unjudged = outcome
                unjudged_total += len(unjudged)
                entry = {
                    "key": row_key(row, corpus),
                    "labels": {chunk_id: grade for chunk_id, grade in judged.items() if grade},
                }
                if unjudged:
                    entry["unjudged"] = unjudged
                cache[entry["key"]] = entry
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        print(
            json.dumps(
                {
                    "graded": min(start + parallel, len(todo)),
                    "of": len(todo),
                    "failed": len(failed),
                    "unjudged": unjudged_total,
                }
            ),
            flush=True,
        )
    await gateway.close()
    await redis.aclose()
    if failed:
        print(json.dumps({"grading_failures": failed}, ensure_ascii=False), flush=True)
    return {key: value["labels"] for key, value in cache.items()}, len(failed), unjudged_total


def stratify(rows: list[dict], target: int, seed: int) -> list[dict]:
    """Round-robin across categories so the frozen set stays near the size while staying spread."""
    if len(rows) <= target:
        return rows
    rng = random.Random(seed)
    by_category: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row)
    for bucket in by_category.values():
        rng.shuffle(bucket)
    picked: list[dict] = []
    for index in range(max(len(bucket) for bucket in by_category.values())):
        for bucket in by_category.values():
            if index < len(bucket) and len(picked) < target:
                picked.append(bucket[index])
        if len(picked) >= target:
            break
    return picked


def coverage(rows: list[dict]) -> dict[str, int]:
    report: dict[str, int] = defaultdict(int)
    for row in rows:
        report[f"文件/{row['file_type'] or '无'}"] += 1
        report[f"来源/{row.get('query_origin', '对照')}"] += 1
        for kind in row["content_kinds"]:
            report[f"内容/{kind}"] += 1
        report[f"难度/{row['difficulty'] or '未知'}"] += 1
    return dict(sorted(report.items()))


def write_rows(path: Path, rows: list[dict]) -> dict:
    body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return {
        "path": str(path.relative_to(ROOT)),
        "queries": len(rows),
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
    }


def write_review(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query", "source", "chunk_id", "model_grade", "human_grade"])
        for row in rows:
            for chunk_id, grade in sorted(row["labels"].items()):
                writer.writerow([row["query"], row["source"], chunk_id, grade, ""])


def apply_overrides(rows: list[dict], path: Path) -> int:
    if not path.exists():
        return 0
    index = {(row["query"], chunk_id): (row, chunk_id) for row in rows for chunk_id in row["labels"]}
    changed = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            grade = (record.get("human_grade") or "").strip()
            found = index.get((record.get("query", ""), record.get("chunk_id", "")))
            if grade.isdigit() and found and int(grade) != found[0]["labels"][found[1]]:
                found[0]["labels"][found[1]] = int(grade)
                changed += 1
    return changed


async def build(
    version: str,
    target: int,
    seed: int,
    parallel: int,
    limit: int | None = None,
    pinned_corpus: str | None = None,
) -> dict:
    settings = Settings()
    engine = make_engine(settings)
    sessions = make_session_factory(engine)
    credentials = dotenv_values(ROOT / "data/admin-credentials.txt")
    try:
        async with sessions() as session:
            documents = list(
                await session.scalars(
                    select(Document).where(Document.status == "ready").order_by(Document.id)
                )
            )
            buckets = await enumerate_queries(session, documents)
            live_revision = str(await revision(session))
            if pinned_corpus and pinned_corpus != live_revision:
                # Pooling and grading are keyed by the knowledge revision, so an unrelated upload/delete
                # churn would otherwise throw away hours of labels. Reuse is only safe because the build
                # below refuses to grade a pool whose chunk no longer exists.
                print(
                    json.dumps(
                        {"reusing_labels_pooled_against": pinned_corpus, "live_revision": live_revision},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            corpus = pinned_corpus or live_revision
            enumerated = sum(len(bucket) for bucket in buckets.values())
            if limit:
                buckets = {name: rows[:limit] for name, rows in buckets.items()}
            controls = []
            for topic in CONTROL_TOPICS:
                if await fuzzy_search(session, topic, []):
                    raise RuntimeError(f"对照 Query“{topic}”在语料中存在近似命中，需更换")
                controls.append(
                    {
                        "query": topic,
                        "doc_id": None,
                        "source": None,
                        "file_type": None,
                        "category": "对照/语料外",
                        "difficulty": None,
                        "content_kinds": ["语料外"],
                        "seed_chunk_id": None,
                        "labels": {},
                        "control": True,
                    }
                )
        pool_size = 0
        # Grade a 40% headroom buffer because queries whose own section is not judged a direct answer get dropped.
        buckets["frozen"] = stratify(buckets["frozen"], int(target * 1.4), seed)
        rows = [*buckets["frozen"], *buckets["tuning"]]
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as admin:
            login = await admin.post(
                "/v1/admin/auth/login",
                json={"username": credentials["username"], "password": credentials["password"]},
            )
            login.raise_for_status()
            headers = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
            created = await admin.post(
                "/v1/admin/api-keys",
                headers=headers,
                json={
                    "name": f"dataset pooling {time.time_ns()}",
                    # The revoke below cannot run if the process is killed, so the key must expire by itself.
                    "expires_at": (datetime.now(UTC) + timedelta(hours=8)).isoformat(),
                    "rate_limit_per_minute": 900,
                    # Keys default to 1000 requests/day, which stalls a several-thousand-request build.
                    "rate_limit_per_day": 200_000,
                },
            )
            created.raise_for_status()
            secret = created.json()["data"]
            try:
                pools = await collect_pools(
                    rows, secret["key"], ROOT / "data/evaluation" / f"pool-{version}.jsonl", parallel, corpus
                )
                needed = {chunk_id for pool_ids in pools.values() for chunk_id in pool_ids}
                async with sessions() as session:
                    texts = {
                        chunk_id: text
                        for chunk_id, text in (
                            await session.execute(
                                select(Chunk.chunk_id, Chunk.text).where(Chunk.chunk_id.in_(needed))
                            )
                        ).all()
                    }
                stale = needed - set(texts)
                if stale:
                    raise RuntimeError(
                        f"池化缓存里有 {len(stale)} 个分块已不存在，标签不再对应当前语料，"
                        "请去掉 --corpus-revision 重新池化"
                    )
                grades, grading_failures, unjudged_candidates = await grade_pools(
                    rows,
                    pools,
                    texts,
                    ROOT / "data/evaluation" / f"grades-{version}.jsonl",
                    parallel,
                    corpus,
                )
            finally:
                # A build outlives the admin token, so re-login rather than reuse the header that created the key.
                renewed = await admin.post(
                    "/v1/admin/auth/login",
                    json={"username": credentials["username"], "password": credentials["password"]},
                )
                renewed.raise_for_status()
                revoked = await admin.delete(
                    f"/v1/admin/api-keys/{secret['id']}",
                    headers={"Authorization": "Bearer " + renewed.json()["data"]["access_token"]},
                )
                print(json.dumps({"revoked_pool_key": revoked.status_code}), flush=True)
        before = len(rows)
        for row in rows:
            row["labels"] = grades.get(row_key(row, corpus), {})
            pool_size = max(pool_size, len(pools.get(row_key(row, corpus), [])))
        for bucket in buckets.values():
            # A query whose own section is not judged a direct answer is a bad auto-generated query.
            bucket[:] = [row for row in bucket if row["labels"].get(row["seed_chunk_id"]) == 2]
        dropped = before - sum(len(bucket) for bucket in buckets.values())
        frozen = stratify(buckets["frozen"], target, seed) + controls
        overrides = apply_overrides(frozen, ROOT / "data/evaluation" / f"overrides-{version}.csv")
        paths = {
            "frozen": write_rows(ROOT / "data/evaluation" / f"frozen-{version}.jsonl", frozen),
            "tuning": write_rows(ROOT / "data/evaluation" / f"tuning-{version}.jsonl", buckets["tuning"]),
        }
        write_review(ROOT / "data/evaluation" / f"review-frozen-{version}.csv", frozen)
    finally:
        await engine.dispose()
    sizes = [len(row["labels"]) for row in frozen if not row.get("control")]
    grade_counts: dict[str, int] = defaultdict(int)
    for row in frozen:
        for grade in row["labels"].values():
            grade_counts[str(grade)] += 1
    return {
        "version": version,
        "pooling": "pooled-llm-judge",
        "corpus_revision": corpus,
        "documents": len(documents),
        "enumerated_queries": enumerated,
        "candidate_queries": len(rows),
        "dropped_queries_without_seed_grade_2": dropped,
        "grading_failures": grading_failures,
        "unjudged_candidates": unjudged_candidates,
        "human_overrides_applied": overrides,
        "sets": paths,
        "frozen_coverage": coverage(frozen),
        "frozen_categories": dict(sorted(Counter(row["category"] for row in frozen).items())),
        "labels_per_query": {
            "min": min(sizes, default=0),
            "median": sorted(sizes)[len(sizes) // 2] if sizes else 0,
            "max": max(sizes, default=0),
        },
        "max_pool_size": pool_size,
        "grade_counts": dict(sorted(grade_counts.items())),
        "label_scale": {"2": "直接回答", "1": "部分覆盖或必要背景", "0": "不相关（含未标注）"},
        "rubric_sha256": hashlib.sha256(GRADE_RUBRIC.encode()).hexdigest(),
    }


if __name__ == "__main__":
    argument = argparse.ArgumentParser()
    argument.add_argument("--version", default="2026-09")
    argument.add_argument("--frozen-target", type=int, default=320)
    argument.add_argument("--seed", type=int, default=20260925)
    argument.add_argument("--parallel", type=int, default=6)
    argument.add_argument("--limit", type=int, help="每个集合只取前 N 条候选 Query，用于冒烟验证")
    argument.add_argument(
        "--corpus-revision",
        help="复用按该知识库修订号池化好的标注缓存，只补跑新增或缺失的 Query；脚本会校验缓存里的分块都还在",
    )
    args = argument.parse_args()
    print(
        json.dumps(
            asyncio.run(
                build(
                    args.version,
                    args.frozen_target,
                    args.seed,
                    args.parallel,
                    args.limit,
                    args.corpus_revision,
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
