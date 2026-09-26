from uuid import uuid4

import pytest
from acceptance_quality import ai_tag_links

from app.models import Chunk, ChunkTag, Document, Tag


async def _link(session, document, tag_name: str, review_status: str, source: str, index: int):
    tag = Tag(name=tag_name, normalized_name=tag_name.casefold(), review_status=review_status)
    session.add(tag)
    await session.flush()
    chunk = Chunk(
        chunk_id="chunk_" + uuid4().hex,
        doc_id=document.id,
        chunk_index=index,
        text="Redis 缓存穿透的处理方式",
        text_with_context="Redis 缓存穿透的处理方式",
        char_count=14,
        token_count=8,
        qdrant_point_id=uuid4(),
    )
    session.add(chunk)
    await session.flush()
    session.add(ChunkTag(chunk_id=chunk.id, tag_id=tag.id, source=source, confidence=0.7))
    return chunk


async def test_tag_accuracy_samples_every_ai_link_not_just_auto_approved_ones(context):
    """A confidence gate approved 12 of 2444 tags; sampling only those measured the easiest 0.5%."""
    async with context["sessions"]() as session:
        ready = Document(
            doc_id="doc_" + uuid4().hex,
            title="Ready",
            original_filename="ready.pdf",
            file_type="pdf",
            file_size=100,
            source_path="documents/ready.pdf",
            status="ready",
            total_chunks=3,
        )
        drafting = Document(
            doc_id="doc_" + uuid4().hex,
            title="Draft",
            original_filename="draft.pdf",
            file_type="pdf",
            file_size=100,
            source_path="documents/draft.pdf",
            status="pending",
            total_chunks=1,
        )
        session.add_all([ready, drafting])
        await session.flush()
        await _link(session, ready, "已过门", "approved", "auto", 0)
        await _link(session, ready, "待审核", "pending", "auto", 1)
        await _link(session, ready, "规则命中", "pending", "rule", 2)
        await _link(session, drafting, "未就绪", "pending", "auto", 3)
        await session.commit()

        names = {row[2] for row in await ai_tag_links(session)}

    assert names == {"已过门", "待审核"}


async def test_accuracy_sampling_sees_the_context_the_tagger_saw(context):
    """Chunk.text is the bare body; tagging ran on text_with_context, so the judge must read the same thing."""
    async with context["sessions"]() as session:
        document = Document(
            doc_id="doc_" + uuid4().hex,
            title="Ctx",
            original_filename="ctx.pdf",
            file_type="pdf",
            file_size=100,
            source_path="documents/ctx.pdf",
            status="ready",
        )
        session.add(document)
        await session.flush()
        chunk = await _link(session, document, "缓存穿透", "pending", "auto", 0)
        chunk.text = "裸正文"
        chunk.text_with_context = "[Redis > 缓存] 带标题链的正文"
        await session.commit()
        public_id, body = chunk.chunk_id, chunk.text_with_context
        rows = await ai_tag_links(session)

    assert rows[0][0] == public_id
    assert rows[0][1] == body


async def test_coverage_counts_only_approved_tags_of_ready_documents(context):
    """方案 8.2 说的是"有效标签"，而检索、filters 与 Payload 只认已过审标签，口径必须一致。"""
    from acceptance_quality import check_tags

    async with context["sessions"]() as session:
        ready = Document(
            doc_id="doc_" + uuid4().hex,
            title="Cov",
            original_filename="cov.pdf",
            file_type="pdf",
            file_size=100,
            source_path="documents/cov.pdf",
            status="ready",
        )
        session.add(ready)
        await session.flush()
        await _link(session, ready, "已过门", "approved", "auto", 0)
        await _link(session, ready, "待审核", "pending", "auto", 1)
        untagged = Chunk(
            chunk_id="chunk_" + uuid4().hex,
            doc_id=ready.id,
            chunk_index=2,
            text="没有标签的分块",
            text_with_context="没有标签的分块",
            char_count=7,
            token_count=4,
            qdrant_point_id=uuid4(),
        )
        session.add(untagged)
        await session.commit()
        checks: dict = {}
        await check_tags(session, checks)

    coverage = checks["标签覆盖率"]
    assert coverage["approved_only"] is True
    assert coverage["ready_chunks"] == 3
    assert coverage["chunks_with_tags"] == 1
    assert coverage["coverage"] == round(1 / 3, 4)
    assert coverage["passed"] is False


def test_tag_link_signals_explain_verdicts_without_trusting_the_judge():
    from acceptance_quality import tag_link_signals

    body = tag_link_signals("[Redis > 缓存穿透] 缓存穿透用布隆过滤器解决", "缓存穿透")
    assert body == {"tag_in_body": True, "only_in_heading": False, "table_fragment": False}
    heading_only = tag_link_signals("[数据库事务 > 分布式事务] 两段式提交与三阶段提交", "分布式事务")
    assert heading_only["only_in_heading"] is True and heading_only["tag_in_body"] is False
    fragment = tag_link_signals(
        "[前端开发 > 作用域提升] | Scope | | Hoisting | | --- | --- |" + " | x |" * 10, "Scope"
    )
    assert fragment["table_fragment"] is True


def test_the_exported_sample_is_readable_as_the_override_file(tmp_path):
    """A human edits the exported file and saves it as the overrides input, so the writer and the reader
    must agree on the column names — a header change would silently drop every human verdict."""
    import csv

    from acceptance_quality import REVIEW_COLUMNS, read_tag_overrides

    path = tmp_path / "tag-accuracy-overrides.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "chunk_id": "chunk_a",
                "tag": "缓存, 穿透",
                "ai_verdict": "ok",
                "human_verdict": "bad",
                "tag_in_body": True,
                "only_in_heading": False,
                "table_fragment": False,
                "chunk_excerpt": "正文里有逗号, 会被引号包住",
            }
        )
    assert read_tag_overrides(path) == {("chunk_a", "缓存, 穿透"): ("bad", "human")}


def test_human_verdicts_beat_the_model_that_graded_its_own_tags(tmp_path):
    from acceptance_quality import read_tag_overrides

    path = tmp_path / "tag-accuracy-overrides.csv"
    path.write_text(
        "chunk_id,tag,ai_verdict,human_verdict,verdict_by\n"
        "chunk_a,缓存穿透,ok,bad,\n"
        "chunk_b,Redis,ok,\n"
        "chunk_c,Kafka,bad,大概吧,\n"
        "chunk_d,Netty,ok,bad,agent:qoder\n",
        encoding="utf-8-sig",
    )
    overrides = read_tag_overrides(path)
    # Only an explicit ok/bad counts as a ratified verdict; a blank cell leaves the model's answer standing.
    # An empty verdict_by means a person filled it in; anything else is a machine pass and must stay labelled.
    assert overrides == {
        ("chunk_a", "缓存穿透"): ("bad", "human"),
        ("chunk_d", "Netty"): ("bad", "agent:qoder"),
    }
    assert read_tag_overrides(tmp_path / "missing.csv") == {}


def test_resampling_the_tag_population_does_not_void_the_human_verdicts():
    """The human fills in whatever the last run exported, so a re-run must keep drawing the same links.

    ``random.Random(seed).sample`` changed the whole set whenever any link appeared or disappeared, which threw
    away filled-in verdicts while the report still looked internally consistent.
    """
    from acceptance_quality import stable_tag_sample

    rows = [(f"chunk_{i:03d}", "正文", f"tag_{i}") for i in range(60)]
    first = stable_tag_sample(rows, 12, 20260926)
    assert len(first) == 12
    assert stable_tag_sample(rows, 12, 20260926) == first
    assert stable_tag_sample(rows, 12, 20260927) != first

    previous = [[row[0], row[2]] for row in first]
    # 新增关联即使哈希排在最前，也不许挤掉人工判定已经写过的样本。
    assert stable_tag_sample([("chunk_900", "正文", "aaa"), *rows], 12, 20260926, previous) == first
    # 抽中的关联被删掉时只补缺口，其余原样保留。
    survivors = [row for row in rows if row[0] != first[0][0]]
    carried = stable_tag_sample(survivors, 12, 20260926, previous)
    assert [row[0] for row in carried[:11]] == [row[0] for row in first[1:]]
    assert len(carried) == 12 and carried[11][0] not in {row[0] for row in first}


def test_the_sample_manifest_round_trips_into_the_next_draw(tmp_path):
    """The script writes the manifest on one run and reads it on the next, so the key format must agree —
    drift would re-draw the sample and silently discard the verdicts the human already filled in."""
    import json

    from acceptance_quality import read_sample_manifest, stable_tag_sample, write_sample_manifest

    rows = [(f"chunk_{i:03d}", "正文", f"tag_{i}") for i in range(40)]
    path = tmp_path / "tag-accuracy-sample.json"
    assert read_sample_manifest(path) == []
    first = stable_tag_sample(rows, 8, 20260926)
    write_sample_manifest(path, first)
    assert json.loads(path.read_text(encoding="utf-8")) == sorted([[row[0], row[2]] for row in first])
    again = stable_tag_sample(rows, 8, 20260926, read_sample_manifest(path))
    # 清单按 key 排序存，回读顺序不同，抽中的关联必须一模一样。
    assert sorted([[row[0], row[2]] for row in again]) == sorted([[row[0], row[2]] for row in first])
    path.write_text("not json at all", encoding="utf-8")
    assert read_sample_manifest(path) == []


def test_bulk_tag_approval_refuses_without_a_passing_accuracy_number_written_by_the_same_report(tmp_path):
    """promote_tags reads what acceptance_quality writes, so the two must agree on key names."""
    import json

    from acceptance_quality import MIN_TAG_SAMPLES, record
    from promote_tags import accuracy_evidence

    path = tmp_path / "quality-report.json"
    checks: dict = {}
    record(
        checks,
        "标签准确率",
        False,
        {"accuracy": 0.42, "judged_links": MIN_TAG_SAMPLES, "judged_by": "Qwen/Qwen3-14B"},
    )
    path.write_text(json.dumps({"checks": checks}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        accuracy_evidence(path)

    checks: dict = {}
    record(
        checks,
        "标签准确率",
        True,
        {"accuracy": 0.93, "judged_links": MIN_TAG_SAMPLES, "judged_by": "Qwen/Qwen3-14B"},
    )
    path.write_text(json.dumps({"checks": checks}, ensure_ascii=False), encoding="utf-8")
    assert accuracy_evidence(path)["accuracy"] == 0.93

    with pytest.raises(SystemExit):
        accuracy_evidence(tmp_path / "absent.json")
