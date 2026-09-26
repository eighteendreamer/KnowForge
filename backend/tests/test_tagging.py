import json

import httpx
import pytest

from app.core.errors import AppError
from app.services.tagging.tagger import resolve_category, rule_tags, tag_chunk

CATEGORIES = ["后端开发/缓存/Redis", "前端开发/性能优化"]


def chat(payload: dict):
    return httpx.Response(
        200,
        json={
            "id": "test",
            "object": "chat.completion",
            "created": 1,
            "model": "test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                }
            ],
        },
    )


def test_resolve_category_maps_model_output_onto_the_existing_tree():
    assert resolve_category("后端开发/缓存/Redis", CATEGORIES) == "后端开发/缓存/Redis"
    assert resolve_category("  后端开发/缓存/Redis/ ", CATEGORIES) == "后端开发/缓存/Redis"
    assert resolve_category("后端开发\\缓存\\Redis", CATEGORIES) == "后端开发/缓存/Redis"
    assert resolve_category("后端开发/缓存/Redis/持久化", CATEGORIES) == "后端开发/缓存/Redis"
    assert resolve_category("前端开发", ["前端开发", "前端开发/性能优化"]) == "前端开发"
    assert resolve_category("移动开发/Android", CATEGORIES) is None
    assert resolve_category("", CATEGORIES) is None


def test_rule_tags_match_technology_mentions():
    assert rule_tags("Redis 缓存穿透要用布隆过滤器") == ["Redis", "缓存穿透"]
    assert rule_tags("无关内容") == []


async def test_category_root_names_never_survive_as_tags_but_leaf_names_do(context):
    """ "后端开发" is classification, not a label; "Redis" is a leaf of the tree and is the tag worth keeping."""

    def handler(request):
        return chat(
            {
                "tags": ["后端开发", "前端开发", "后端开发/缓存/Redis", "Redis", "缓存穿透"],
                "category": "后端开发/缓存/Redis",
                "difficulty": "中级",
            }
        )

    gateway = __import__("test_models").mock_gateway(context, handler)
    try:
        result = await tag_chunk("Redis 缓存穿透", CATEGORIES, [], gateway)
        assert result.tags == ["Redis", "缓存穿透"]
    finally:
        await gateway.close()


def test_taxonomy_names_drop_root_nodes_but_keep_leaf_technologies():
    from app.services.tagging.tagger import taxonomy_names

    names = taxonomy_names(["后端开发/缓存/Redis", "前端开发/性能优化"])
    assert {"后端开发", "前端开发", "后端开发/缓存/redis"} <= names
    # Redis and Kafka are leaves of the tree; they are the tags worth keeping, so they must not be blocked.
    assert "redis" not in names
    assert "性能优化" not in names


async def test_tag_chunk_retries_once_then_keeps_valid_category(context):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["messages"][1]["content"])
        if len(calls) == 1:
            return chat(
                {"tags": ["Redis", " 缓存穿透 "], "category": "缓存/Redis 深度剖析", "difficulty": "中级"}
            )
        return chat({"tags": ["Redis", "缓存穿透"], "category": "后端开发/缓存/Redis", "difficulty": "中级"})

    gateway = __import__("test_models").mock_gateway(context, handler)
    try:
        result = await tag_chunk("Redis 缓存穿透", CATEGORIES, ["Redis"], gateway)
        assert len(calls) == 2
        assert "correction" in calls[1]
        assert result.category == "后端开发/缓存/Redis"
        assert result.tags == ["Redis", "缓存穿透"]
        assert result.difficulty == "中级"
    finally:
        await gateway.close()


async def test_tag_chunk_leaves_category_empty_instead_of_failing_the_document(context):
    def handler(request):
        return chat({"tags": ["Kafka"], "category": "消息队列/Kafka 实战", "difficulty": "高级"})

    gateway = __import__("test_models").mock_gateway(context, handler)
    try:
        result = await tag_chunk("Kafka 消费者提交", CATEGORIES, [], gateway)
        assert result.category == ""
        assert result.tags == ["Kafka"]
    finally:
        await gateway.close()


async def test_tag_chunk_drops_category_paths_out_of_tags(context):
    def handler(request):
        return chat(
            {
                "tags": ["Redis", "后端开发/缓存/Redis", "前端开发/性能优化", "持久化"],
                "category": "后端开发/缓存/Redis",
                "difficulty": "中级",
            }
        )

    gateway = __import__("test_models").mock_gateway(context, handler)
    try:
        result = await tag_chunk("Redis 持久化策略", CATEGORIES, [], gateway)
        assert result.tags == ["Redis", "持久化"]
    finally:
        await gateway.close()


async def test_tag_chunk_rejects_documents_without_any_tag(context):
    def handler(request):
        return chat({"tags": ["  ", ""], "category": "前端开发/性能优化", "difficulty": "初级"})

    gateway = __import__("test_models").mock_gateway(context, handler)
    try:
        with pytest.raises(AppError, match="未返回有效标签"):
            await tag_chunk("普通文本", CATEGORIES, [], gateway)
    finally:
        await gateway.close()
