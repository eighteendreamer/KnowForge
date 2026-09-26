from app.services.retrieval.intent import detect_strategy


def test_identifier_shaped_queries_route_to_the_literal_path():
    """术语与标识符检索走 keyword：词面命中优先，还省掉一次远程向量调用。"""
    assert detect_strategy("CompletableFuture") == "keyword"
    assert detect_strategy("notify()") == "keyword"
    assert detect_strategy("Kafka acks") == "keyword"


def test_narrative_and_question_shaped_queries_route_to_hybrid():
    assert detect_strategy("Redis 缓存穿透是如何解决的？") == "hybrid"
    assert detect_strategy("怎么保证消息不丢") == "hybrid"
    assert detect_strategy("缓存") == "hybrid"


def test_intent_sees_simplified_text_before_choosing():
    """繁体提问要先归一再判定，否则疑问词表命中不了，会被误当成词面型。"""
    assert detect_strategy("緩存穿透怎麼解決") == "hybrid"


def test_empty_query_falls_back_to_the_recommended_default():
    assert detect_strategy("   ") == "hybrid"
