from app.services.retrieval.bm25_encoder import bm25_vector, terms
from app.services.retrieval.normalize import drop_stopwords, to_simplified
from app.services.retrieval.search_service import group_adjacent, join_overlapped


def test_traditional_query_encodes_to_the_same_terms_as_the_simplified_one():
    """语料是简体的，繁体提问必须编出同一组词项，否则 BM25 一路直接落空。"""
    assert to_simplified("緩存穿透是如何解決的？") == "缓存穿透是如何解决的？"
    traditional = bm25_vector("緩存穿透", query=True)
    simplified = bm25_vector("缓存穿透", query=True)
    assert traditional.indices == simplified.indices


def test_document_side_encoding_keeps_the_stored_word_shapes():
    """查询侧归一不能顺手改文档侧：那会让已写进 Qdrant 的稀疏向量与查询不同形。"""
    assert bm25_vector("緩存穿透").indices != bm25_vector("缓存穿透").indices


def test_stopwords_are_dropped_from_the_query_but_never_leave_it_empty():
    kept = drop_stopwords(terms("缓存穿透是如何解决的"))
    assert "缓存" in kept and "穿透" in kept
    assert not {"是", "如何", "的"}.intersection(kept)
    assert drop_stopwords(["的", "了"]) == ["的", "了"]


def test_adjacent_chunks_of_the_same_document_form_one_group_in_rank_order():
    ranked = [("a", 7, 3), ("b", 7, 2), ("c", 7, 9), ("d", 8, 2)]
    assert group_adjacent(ranked, merge=True) == [["a", "b"], ["c"], ["d"]]
    assert group_adjacent(ranked, merge=False) == [["a"], ["b"], ["c"], ["d"]]


def test_merged_text_does_not_repeat_the_chunk_overlap():
    assert join_overlapped(["Redis 缓存穿透用布隆过滤器拦截", "布隆过滤器拦截需要预估数据量"]) == (
        "Redis 缓存穿透用布隆过滤器拦截需要预估数据量"
    )
    assert join_overlapped(["第一段", "第二段"]) == "第一段第二段"
