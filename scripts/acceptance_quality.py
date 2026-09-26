"""Run the plan's §8.2 parse-and-tag quality checks against the real ingestion output.

Everything except ``--tags`` is read from persisted artifacts (AST JSON, PostgreSQL, Qdrant payloads),
so the structural half of §8.2 costs no model calls. ``--tags`` samples real AI tag links and asks the
configured LLM whether each tag is supported by its chunk, writing a review CSV; verdicts copied into the
overrides CSV win over the model, because the judge is the same model that produced the tags.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.core.model_client import ModelGateway
from app.models import Category, Chunk, ChunkTag, Document, Tag
from app.services.runtime_config import active_settings

ROOT = Path(__file__).resolve().parents[1]
DIFFICULTIES = {"初级", "中级", "高级"}
GARBLED_RATIO = 0.05
# The judge must see at least what the tagger saw: tagging ran on text_with_context, which carries the [A > B > C]
# section prefix on top of the chunk body.
CANDIDATE_CHARS = 2400
MIN_TAG_SAMPLES = 100
# 一个声称"人工已确认"的门，不能被模型（包括复核用的模型）满足：判官与打标方同源。
# 所以 ratified 判定要带作者，只有 human 作者的条目才计入人工背书数量。
MIN_HUMAN_VERDICTS = 100
TAG_OVERRIDE_FILE = ROOT / "data/acceptance/tag-accuracy-overrides.csv"
# The drawn (chunk_id, tag) pairs are persisted so a re-run keeps the same links: human verdicts are written
# against that set, and re-drawing them would silently discard the work.
TAG_SAMPLE_MANIFEST = ROOT / "data/acceptance/tag-accuracy-sample.json"
# The exported sample is the file a human edits and saves back as the overrides file, so the columns the reader
# needs must be the columns the writer emits.
REVIEW_COLUMNS = [
    "chunk_id",
    "tag",
    "ai_verdict",
    "human_verdict",
    "tag_in_body",
    "only_in_heading",
    "table_fragment",
    "chunk_excerpt",
    "verdict_by",
]
NOISE = re.compile(r"<\s*(script|style|nav|iframe)|function\s*\(|addEventListener", re.IGNORECASE)
TAG_RUBRIC = """判断标签是否被该段落正文支持。规则：
正确 = 段落内容确实在讲这个技术点，标签是合适的检索入口。
错误 = 段落与该标签无关，或标签是凭空臆造/明显跑题。
输入 JSON：{"t": 标签, "c": 段落}。只输出 JSON：{"verdict": "ok" 或 "bad"}。"""


def garbled_ratio(value: str) -> float:
    return value.count("\ufffd") / max(1, len(value))


def record(checks: dict, name: str, passed: bool, detail: dict) -> None:
    checks[name] = {"passed": passed, **detail}


async def check_parsing(session: AsyncSession, settings: Settings, checks: dict) -> None:
    documents = list(
        await session.scalars(
            select(Document).where(Document.status == "ready", Document.ast_path.is_not(None))
        )
    )
    pdf_pages: list[float] = []
    noisy: list[str] = []
    garbled: list[str] = []
    empty = []
    for document in documents:
        raw = json.loads((settings.storage_path / document.ast_path).read_text(encoding="utf-8"))
        elements = raw["elements"]
        total_pages = raw["stats"].get("total_pages") or 0
        if not elements:
            empty.append(document.original_filename)
        if document.file_type == "pdf" and total_pages:
            covered = {element["page"] for element in elements if element.get("page")}
            pdf_pages.append(len(covered) / total_pages)
        for element in elements:
            if document.file_type == "html" and NOISE.search(element["text"]):
                noisy.append(f"{document.original_filename}#{element['page']}")
            if garbled_ratio(element["text"]) > GARBLED_RATIO:
                garbled.append(f"{document.original_filename}#{element['page']}")
    record(
        checks,
        "文本PDF解析",
        bool(pdf_pages) and min(pdf_pages) >= 0.8 and not empty,
        {
            "documents": len(documents),
            "pdf_page_coverage_min": round(min(pdf_pages), 4) if pdf_pages else None,
            "pdf_page_coverage_mean": round(sum(pdf_pages) / len(pdf_pages), 4) if pdf_pages else None,
            "documents_without_elements": empty,
        },
    )
    record(
        checks,
        "无明显乱码",
        not garbled,
        {"elements_over_garbled_ratio": len(garbled), "samples": garbled[:5], "ratio_limit": GARBLED_RATIO},
    )
    record(
        checks,
        "HTML噪声清除",
        not noisy,
        {"elements_with_script_or_nav": len(noisy), "samples": noisy[:5]},
    )


async def check_scanned_pages(session: AsyncSession, checks: dict) -> None:
    failures = (
        await session.execute(
            select(Document.original_filename, Document.parse_error).where(Document.parse_error.is_not(None))
        )
    ).all()
    ready_empty = list(
        await session.scalars(
            select(Document.original_filename).where(Document.status == "ready", Document.total_chunks == 0)
        )
    )
    record(
        checks,
        "扫描页识别留痕",
        not ready_empty,
        {
            "documents_with_parse_error": [name for name, _ in failures],
            "ready_documents_without_chunks": ready_empty,
            "rule": "失败原因必须写在 parse_error，ready 且零分块视为静默通过",
        },
    )


async def check_chunks(session: AsyncSession, settings: Settings, checks: dict) -> None:
    total = await session.scalar(select(func.count()).select_from(Chunk))
    paths = (
        await session.execute(
            select(
                Chunk.doc_id,
                Chunk.chunk_index,
                func.coalesce(func.array_length(Chunk.section_path, 1), 0),
            ).join(Document, Chunk.doc_id == Document.id)
        )
    ).all()
    by_document: dict[int, list[tuple[int, int]]] = {}
    for doc_id, chunk_index, depth in paths:
        by_document.setdefault(doc_id, []).append((chunk_index, depth))
    # An empty parent chain is only a defect after a heading was already seen; content before the first
    # heading of a document genuinely has no ancestors.
    section_violations = []
    headingless = []
    for doc_id, items in by_document.items():
        headed = [index for index, depth in items if depth]
        if not headed:
            headingless.append(doc_id)
            continue
        if any(depth == 0 and index >= min(headed) for index, depth in items):
            section_violations.append(doc_id)
    no_section = sum(1 for _, _, depth in paths if depth == 0)
    over_limit = await session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.token_count > settings.embedding_max_tokens)
    )
    zero_tokens = await session.scalar(select(func.count()).select_from(Chunk).where(Chunk.token_count <= 0))
    table_rows = (
        await session.execute(
            select(Chunk.chunk_id, Chunk.token_count).where(Chunk.element_types.contains(["Table"]))
        )
    ).all()
    oversized_tables = [chunk_id for chunk_id, tokens in table_rows if tokens > settings.embedding_max_tokens]
    record(
        checks,
        "Chunk完整性",
        not section_violations and zero_tokens == 0 and over_limit == 0,
        {
            "chunks": total,
            "chunks_without_parent_chain_before_first_heading": no_section,
            "documents_with_parent_chain_gap": section_violations,
            "documents_without_any_heading": headingless,
            "non_positive_token_count": zero_tokens,
            "over_embedding_limit": over_limit,
            "embedding_max_tokens": settings.embedding_max_tokens,
            "token_count_source": "HuggingFace tokenizer 实测值，非固定中文字符比例",
        },
    )
    record(
        checks,
        "超长表格不静默截断",
        not oversized_tables,
        {
            "table_chunks": len(table_rows),
            "over_limit": oversized_tables[:5],
            "rule": "超限表格整表保留，超限即处理失败并留痕",
        },
    )


async def check_tags(session: AsyncSession, checks: dict) -> None:
    taggable = await session.scalar(
        select(func.count(func.distinct(Chunk.id)))
        .join(Document, Chunk.doc_id == Document.id)
        .where(Document.status == "ready")
    )
    # 方案 8.2 说的是"至少有一个有效标签"，而下游（Payload、filters.tags、响应 tags）只认已过审标签，
    # 所以分子必须同时限定 ready 文档与 approved 标签，否则数的是没人能用的关联。
    covered = await session.scalar(
        select(func.count(func.distinct(ChunkTag.chunk_id)))
        .join(Chunk, Chunk.id == ChunkTag.chunk_id)
        .join(Document, Chunk.doc_id == Document.id)
        .join(Tag, ChunkTag.tag_id == Tag.id)
        .where(Document.status == "ready", Tag.review_status == "approved")
    )
    duplicates = (
        await session.execute(
            select(func.lower(func.trim(Tag.name)), func.count())
            .select_from(Tag)
            .group_by(func.lower(func.trim(Tag.name)))
            .having(func.count() > 1)
        )
    ).all()
    categories = {path for (path,) in await session.execute(select(Category.path))}
    bad_category = await session.scalar(
        select(func.count())
        .select_from(Chunk)
        .join(Document, Chunk.doc_id == Document.id)
        .where(
            Document.status == "ready",
            Chunk.category_id.is_not(None),
            ~Chunk.category_id.in_(select(Category.id)),
        )
    )
    bad_difficulty = await session.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.difficulty.is_not(None), Chunk.difficulty.notin_(DIFFICULTIES))
    )
    missing_difficulty = await session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.difficulty.is_(None))
    )
    coverage = covered / taggable if taggable else 0
    record(
        checks,
        "标签覆盖率",
        coverage >= 0.95,
        {
            "chunks_with_tags": covered,
            "ready_chunks": taggable,
            "coverage": round(coverage, 4),
            "approved_only": True,
            "target": 0.95,
        },
    )
    record(
        checks,
        "标签一致性",
        not duplicates,
        {
            "normalized_duplicate_names": [[name, count] for name, count in duplicates],
            "note": "同名大小写/空格重复视为不一致；Payload 只同步已通过审核的标签",
        },
    )
    record(
        checks,
        "分类与难度枚举",
        bad_category == 0 and bad_difficulty == 0,
        {
            "categories_in_tree": len(categories),
            "chunks_with_unknown_category": bad_category,
            "chunks_with_illegal_difficulty": bad_difficulty,
            "chunks_without_difficulty": missing_difficulty,
        },
    )


async def check_payload(session: AsyncSession, settings: Settings, checks: dict, sample: int) -> None:
    # 默认比对全部 ready 分块（2494 分块一次 scroll 就够，抽样只会把不一致率藏起来）。
    # 真要抽样时按 md5(chunk_id) 排：random() 每轮换一批，就无法判断上一轮的不一致是同一条还是新伤。
    statement = (
        select(Chunk.id, Chunk.chunk_id, Chunk.qdrant_point_id)
        .join(Document, Chunk.doc_id == Document.id)
        .where(Document.status == "ready")
        .order_by(func.md5(Chunk.chunk_id))
    )
    rows = (await session.execute(statement if sample <= 0 else statement.limit(sample))).all()
    # ChunkTag.chunk_id holds the internal integer id, while the Qdrant payload carries the public chunk_*.
    # Only approved tags are indexed, so the comparison must use the same visibility rule as retrieval.
    internal_to_public = {row[0]: row[1] for row in rows}
    tag_map: dict[str, set[str]] = {}
    for internal_id, name in await session.execute(
        select(ChunkTag.chunk_id, Tag.name)
        .join(Tag, Tag.id == ChunkTag.tag_id)
        .where(ChunkTag.chunk_id.in_([row[0] for row in rows]), Tag.review_status == "approved")
    ):
        tag_map.setdefault(internal_to_public[internal_id], set()).add(name)
    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key.get_secret_value() or None,
    )
    mismatches = []
    missing = []
    try:
        points = await client.retrieve(
            settings.qdrant_collection,
            [row[2] for row in rows],
            with_payload=True,
        )
        by_point = {str(point.id): point.payload or {} for point in points}
        for _, chunk_id, point_id in rows:
            payload = by_point.get(str(point_id))
            if payload is None:
                missing.append(chunk_id)
                continue
            if set(payload.get("tags") or []) != tag_map.get(chunk_id, set()):
                mismatches.append(chunk_id)
    finally:
        await client.close()
    record(
        checks,
        "审核后Payload与库内一致",
        not missing and not mismatches,
        {
            "sampled_chunks": len(rows),
            "chunk_scope": "全部 ready 分块" if sample <= 0 else f"按 md5(chunk_id) 取前 {sample} 个",
            "points_missing_in_qdrant": missing[:5],
            "tag_payload_mismatches": mismatches[:5],
        },
    )


async def ai_tag_links(session: AsyncSession) -> list:
    """Every model-generated chunk↔tag link in ready documents — the population 8.2 samples from."""
    return list(
        (
            await session.execute(
                select(Chunk.chunk_id, Chunk.text_with_context, Tag.name)
                .join(ChunkTag, ChunkTag.chunk_id == Chunk.id)
                .join(Tag, Tag.id == ChunkTag.tag_id)
                .join(Document, Chunk.doc_id == Document.id)
                .where(Document.status == "ready", ChunkTag.source == "auto")
                .order_by(Chunk.chunk_id, Tag.name)
            )
        ).all()
    )


def read_tag_overrides(path: Path) -> dict[tuple[str, str], tuple[str, str]]:
    """Ratified verdicts keyed by (public chunk id, tag name); a model grading its own output is not evidence.

    Read by column name so the exported sample CSV can be filled in and saved back as the overrides file.
    Each value carries who wrote it: an empty ``verdict_by`` means a person filled the row in, anything else
    (``agent:...``) is a machine review pass and must not be reported as human endorsement.
    """
    if not path.exists():
        return {}
    overrides: dict[tuple[str, str], tuple[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            verdict = (record.get("human_verdict") or "").strip()
            if verdict in {"ok", "bad"}:
                author = (record.get("verdict_by") or "").strip() or "human"
                overrides[((record.get("chunk_id") or "").strip(), (record.get("tag") or "").strip())] = (
                    verdict,
                    author,
                )
    return overrides


def tag_link_signals(text: str, tag: str) -> dict:
    """Deterministic hints that explain a verdict, so a human can judge without reopening every chunk.

    Deliberately computed from the text alone, not from the model: asking the judge to justify itself would
    change what is being measured, and the judge is the same model that wrote the tags.
    """
    marker = "]" if text.startswith("[") and "]" in text else ""
    heading, body = (text.split(marker, 1) + [""])[:2] if marker else ("", text)
    needle = tag.casefold().strip()
    pipes = body.count("|")
    return {
        "tag_in_body": needle in body.casefold(),
        "only_in_heading": needle not in body.casefold() and needle in heading.casefold(),
        "table_fragment": pipes >= 8 or (len(body.strip()) < 40 and pipes > 0),
    }


def excerpt(text: str, limit: int = 120) -> str:
    return " ".join(text.split())[:limit]


def stable_tag_sample(rows: list, samples: int, seed: int, previous: list[list[str]] | None = None) -> list:
    """Keep the previously drawn links and only top up what the corpus no longer contains.

    ``random.Random(seed).sample()`` re-draws the entire set whenever any link appears or disappears, which
    silently voids every human verdict written against the previous export. Ties are broken by a content hash
    so a first run is still reproducible without a manifest.
    """
    by_key = {(row[0], row[2]): row for row in rows}
    wanted = [(key[0], key[1]) for key in previous or []]
    kept = [by_key[pair] for pair in wanted if pair in by_key][:samples]
    taken = {(row[0], row[2]) for row in kept}
    fresh = sorted(
        (row for row in rows if (row[0], row[2]) not in taken),
        key=lambda row: hashlib.sha256(f"{seed}|{row[0]}|{row[2]}".encode()).digest(),
    )
    return kept + fresh[: max(samples - len(kept), 0)]


def read_sample_manifest(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    try:
        keys = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [[str(item[0]), str(item[1])] for item in keys if isinstance(item, list) and len(item) == 2]


def write_sample_manifest(path: Path, picked: list) -> None:
    """Persist exactly what read_sample_manifest consumes; the two drifting apart would discard human verdicts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted([[row[0], row[2]] for row in picked])
    path.write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")


async def check_tag_accuracy(
    session: AsyncSession, settings: Settings, checks: dict, samples: int, seed: int
) -> None:
    # 抽查的是 AI 标签关联本身，不能只抽已过置信度门的少数标签，否则测的是最顺利的一小撮。
    rows = await ai_tag_links(session)
    population = len(rows)
    previous = read_sample_manifest(TAG_SAMPLE_MANIFEST)
    picked = stable_tag_sample(rows, samples, seed, previous)
    carried = sum(1 for row in picked if (row[0], row[2]) in {tuple(key) for key in previous})
    overrides = read_tag_overrides(TAG_OVERRIDE_FILE)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    gateway = ModelGateway(settings, redis)
    judged: list[tuple[str, str, str, str | None, tuple[str, str] | None]] = []
    judge_failures = 0
    try:
        for public_id, chunk_text, name in picked:
            content = json.dumps({"t": name, "c": chunk_text[:CANDIDATE_CHARS]}, ensure_ascii=False)
            verdict = (await gateway.json_chat(TAG_RUBRIC, content, "batch")).get("verdict")
            ai = verdict if verdict in {"ok", "bad"} else None
            ratified = overrides.get((public_id, name))
            if ai is None and ratified is None:
                judge_failures += 1
                continue
            judged.append((public_id, name, chunk_text, ai, ratified))
    finally:
        await gateway.close()
        await redis.aclose()
    review = ROOT / "data/acceptance/tag-accuracy-sample.csv"
    review.parent.mkdir(parents=True, exist_ok=True)
    columns = REVIEW_COLUMNS
    with review.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for public_id, name, chunk_text, ai, ratified in judged:
            writer.writerow(
                {
                    "chunk_id": public_id,
                    "tag": name,
                    "ai_verdict": ai or "",
                    "human_verdict": ratified[0] if ratified else "",
                    "verdict_by": ratified[1] if ratified else "",
                    **tag_link_signals(chunk_text, name),
                    "chunk_excerpt": excerpt(chunk_text),
                }
            )
    final = [(ratified[0] if ratified else ai) for _, _, _, ai, ratified in judged]
    ok = sum(1 for verdict in final if verdict == "ok")
    accuracy = ok / len(final) if final else 0
    authors = Counter(ratified[1] for _, _, _, _, ratified in judged if ratified)
    human_verdicts = authors.get("human", 0)
    write_sample_manifest(TAG_SAMPLE_MANIFEST, picked)
    # 复核判定按 (chunk_id, tag) 匹配；填了但对不上的行必须报出来，否则会静默按"没人判过"继续。
    sample_keys = {(row[0], row[2]) for row in picked}
    stale_verdicts = sorted(
        f"{chunk_id}/{tag}" for chunk_id, tag in overrides if (chunk_id, tag) not in sample_keys
    )
    record(
        checks,
        "标签准确率",
        accuracy >= 0.85 and len(final) >= MIN_TAG_SAMPLES and human_verdicts >= MIN_HUMAN_VERDICTS,
        {
            "sampled_links": len(picked),
            "carried_over_from_previous_sample": carried,
            "sample_manifest": str(TAG_SAMPLE_MANIFEST.relative_to(ROOT)),
            "sample_fingerprint": hashlib.sha256(
                "|".join(sorted(f"{chunk_id}/{tag}" for chunk_id, tag in sample_keys)).encode()
            ).hexdigest()[:16],
            "judged_links": len(final),
            "judge_failures": judge_failures,
            "human_verdicts_applied": human_verdicts,
            "verdicts_by_author": dict(authors),
            "human_verdicts_required": MIN_HUMAN_VERDICTS,
            "human_verdicts_stale": len(stale_verdicts),
            "stale_verdict_keys": stale_verdicts[:5],
            "minimum_sample": MIN_TAG_SAMPLES,
            "ai_links_in_corpus": population,
            "accurate": ok,
            "accuracy": round(accuracy, 4),
            "target": 0.85,
            "rubric_sha256": hashlib.sha256(TAG_RUBRIC.encode()).hexdigest(),
            "judged_by": settings.llm_model,
            "method": "全部 AI（source=auto）标签关联中按 sha256(seed|chunk_id|tag) 排序取前 N 条（population 变动不重抽）；"
            "判官看到的是打标时同一份 text_with_context，复核判定优先于模型判定，非法判定不计入分母；"
            f"verdict_by 留空才算人手背书，agent 前缀的复核只提升可信度、不算人工确认，"
            f"门要求 accuracy>=0.85 且样本>= {MIN_TAG_SAMPLES} 且人手判定>= {MIN_HUMAN_VERDICTS}",
            "review_file": str(review.relative_to(ROOT)),
            "override_file": str(TAG_OVERRIDE_FILE.relative_to(ROOT)),
        },
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--payload-sample",
        type=int,
        default=0,
        help="比对多少个 ready 分块的 Qdrant payload；0（默认）表示全量比对",
    )
    parser.add_argument("--tags", type=int, default=0, help="抽查多少条 AI 标签关联用于准确率评估")
    parser.add_argument("--seed", type=int, default=20260926)
    args = parser.parse_args()
    if args.tags and args.tags < MIN_TAG_SAMPLES:
        parser.error(f"--tags 至少 {MIN_TAG_SAMPLES} 条，方案 8.2 要求随机抽查不少于 100 个 AI 标签关联")
    settings = Settings()
    engine = make_engine(settings)
    sessions = make_session_factory(engine)
    checks: dict = {}
    try:
        async with sessions() as session:
            runtime = await active_settings(session, settings)
            await check_parsing(session, runtime, checks)
            await check_scanned_pages(session, checks)
            await check_chunks(session, runtime, checks)
            await check_tags(session, checks)
            await check_payload(session, runtime, checks, args.payload_sample)
            if args.tags:
                await check_tag_accuracy(session, runtime, checks, args.tags, args.seed)
    finally:
        await engine.dispose()
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "scope": "方案 8.2 解析与标签质量验收，数据源为真实入库产物",
        "checks": checks,
        "passed": all(item["passed"] for item in checks.values()),
    }
    path = ROOT / "data/acceptance/quality-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, item in checks.items():
        print(f"{'PASS' if item['passed'] else 'FAIL'} {name}: {json.dumps(item, ensure_ascii=False)[:180]}")
    print(json.dumps({"report": str(path), "passed": report["passed"]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
