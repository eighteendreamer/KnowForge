from pathlib import Path

from build_eval_dataset import (
    GRADE_RUBRIC,
    apply_overrides,
    content_kinds_of,
    query_origin,
    query_source,
    read_cache,
    row_key,
    split_documents,
    stratify,
    write_review,
)

from app.models import Chunk, Document


def make_chunk(text: str, **kwargs: object) -> Chunk:
    return Chunk(
        chunk_id=str(kwargs.pop("chunk_id", "chunk_x")),
        doc_id=1,
        chunk_index=0,
        section_path=list(kwargs.pop("section_path", [])),
        text=text,
        text_with_context=text,
        char_count=len(text),
        token_count=max(1, len(text) // 2),
        element_types=["NarrativeText"],
        **kwargs,
    )


def test_query_origin_accepts_questions_and_interrogative_headings():
    assert query_origin("Redis 缓存穿透如何解决？") == "question_heading"
    assert query_origin("1、你知道的List 都有哪些") == "interrogative_heading"
    assert query_origin("布隆过滤器的原理") == "interrogative_heading"
    assert query_origin("概述") is None
    assert query_origin("背景介绍与说明段落，没有任何提问词") is None
    assert query_origin("如何" + "长" * 300) is None


def test_query_source_prefers_the_leaf_and_falls_back_to_the_nearest_question_ancestor():
    leaf = make_chunk("正文", section_path=["Redis", "缓存穿透有哪些解决方案？"])
    assert query_source(leaf) == ("缓存穿透有哪些解决方案？", "question_heading")
    child = make_chunk("正文", section_path=["Redis", "缓存穿透有哪些解决方案？", "方案对比"])
    assert query_source(child) == ("缓存穿透有哪些解决方案？", "ancestor_question_heading")
    assert query_source(make_chunk("正文", section_path=["Redis", "概述", "背景"])) is None
    assert query_source(make_chunk("正文", section_path=[])) is None


def test_content_kinds_detect_tables_code_images_and_cross_pages():
    document = Document(doc_id="doc_x", status="ready", file_type="pdf", original_filename="a.pdf")
    chunk = make_chunk(
        "| 方案 | 说明 |\n| --- | --- |\n```python\nprint(1)\n```\n![架构图](https://x/y.png)",
        section_path=["缓存"],
        page_start=3,
        page_end=5,
    )
    assert content_kinds_of(chunk, document) == ["代码", "图片", "表格", "跨页"]
    assert content_kinds_of(make_chunk("普通正文段落", section_path=["缓存"]), document) == ["纯文本"]
    assert "HTML" in content_kinds_of(make_chunk("普通正文"), Document(doc_id="d", file_type="html"))


def test_split_documents_never_places_one_document_in_both_sets():
    documents = [Document(doc_id=f"doc_{index}", id=index + 1, status="ready") for index in range(60)]
    tuning, frozen = split_documents(documents)
    assert tuning and frozen
    assert not tuning & frozen
    assert tuning | frozen == {document.id for document in documents}


def test_stratify_keeps_every_category_and_stays_deterministic():
    rows = [
        {"query": f"q{index}", "category": "A" if index % 2 else "B", "content_kinds": ["纯文本"]}
        for index in range(40)
    ]
    picked = stratify(rows, 10, seed=7)
    assert len(picked) == 10
    assert {row["query"] for row in picked} == {row["query"] for row in stratify(rows, 10, seed=7)}
    assert {row["category"] for row in picked} == {"A", "B"}
    assert len(stratify(rows[:4], 10, seed=7)) == 4


def test_review_file_round_trips_human_grades(tmp_path: Path):
    rows = [{"query": "缓存穿透如何解决？", "source": "a.pdf", "labels": {"chunk_a": 2, "chunk_b": 1}}]
    review = tmp_path / "review.csv"
    write_review(review, rows)
    assert review.read_text(encoding="utf-8-sig").splitlines()[0] == (
        "query,source,chunk_id,model_grade,human_grade"
    )
    overrides = tmp_path / "overrides.csv"
    overrides.write_text(
        "query,source,chunk_id,model_grade,human_grade\n"
        "缓存穿透如何解决？,a.pdf,chunk_b,1,2\n"
        "缓存穿透如何解决？,a.pdf,chunk_a,2,\n"
        "缓存穿透如何解决？,a.pdf,chunk_a,2,abc\n"
        "不存在的问题,a.pdf,chunk_a,2,0\n",
        encoding="utf-8-sig",
    )
    assert apply_overrides(rows, overrides) == 1
    assert rows[0]["labels"] == {"chunk_a": 2, "chunk_b": 2}
    assert apply_overrides(rows, tmp_path / "missing.csv") == 0


def test_read_cache_skips_blank_lines_and_indexes_by_key(tmp_path: Path):
    path = tmp_path / "pool.jsonl"
    path.write_text('{"key": "abc", "pool": ["chunk_a"]}\n\n', encoding="utf-8")
    assert read_cache(path) == {"abc": {"key": "abc", "pool": ["chunk_a"]}}
    assert read_cache(tmp_path / "absent.jsonl") == {}


def test_row_key_changes_when_the_corpus_revision_changes():
    row = {"doc_id": "doc_x", "query": "问题一", "seed_chunk_id": "chunk_a"}
    assert row_key(row, "11") != row_key(row, "12")
    assert row_key(row, "11") == row_key(dict(row), "11")


def test_grade_rubric_states_grades_and_the_json_contract():
    assert '"grades"' in GRADE_RUBRIC
    assert all(line in GRADE_RUBRIC for line in ("2 =", "1 =", "0 ="))
