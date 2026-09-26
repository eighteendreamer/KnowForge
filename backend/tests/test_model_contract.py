"""方案 8.3 的"模型契约"异常场景：Rerank 数量/索引错误、Retry-After 退避、响应超时。

这三条都是"上游返回了但形状或时间不对"的情况。它们必须变成明确的失败，而不是让脏数据进入索引、
或者让一个慢请求把并发额度占死（后者正是"一个路由卡死全站"的原始症状）。
"""

import json
import time

import httpx
import pytest
from openai import AsyncOpenAI

from app.core.errors import AppError
from app.core.model_client import ModelGateway

CHAT_OK = json.dumps(
    {
        "choices": [{"message": {"content": '{"answer":"ok"}'}, "finish_reason": "stop"}],
        "model": "test",
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }
)


def retry_gateway(context, handler, **updates):
    client = AsyncOpenAI(
        api_key="test-only",
        base_url="https://model.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return ModelGateway(context["settings"].model_copy(update=updates), context["app"].state.redis, client)


@pytest.mark.parametrize(
    "results,why",
    [
        ([{"index": 0, "relevance_score": 0.9}], "少返回一条"),
        (
            [
                {"index": 0, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.8},
                {"index": 2, "relevance_score": 0.7},
            ],
            "索引重复",
        ),
    ],
)
async def test_rerank_with_wrong_count_or_index_fails_instead_of_misordering(context, results, why):
    gateway = retry_gateway(
        context,
        lambda request: httpx.Response(200, json={"results": results}),
        model_max_retries=0,
    )
    try:
        with pytest.raises(AppError) as error:
            await gateway.rerank("缓存穿透", ["A", "B", "C"])
        assert error.value.code == 5002, why
        assert "索引或数量" in error.value.message
    finally:
        await gateway.close()


async def test_provider_retry_after_header_sets_the_backoff_instead_of_the_default(context):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=json.loads(CHAT_OK))

    gateway = retry_gateway(context, handler, model_max_retries=1)
    started = time.perf_counter()
    try:
        assert await gateway.json_chat("只回 JSON", "查询") == {"answer": "ok"}
        elapsed = time.perf_counter() - started
        assert len(seen) == 2
        # 指数退避在这里只需约 1 秒；等待接近 3 秒才说明真正服从了上游给的 Retry-After。
        assert 2.5 <= elapsed < 6, elapsed
    finally:
        await gateway.close()


async def test_timeout_is_reported_and_the_model_slot_is_released(context):
    gateway = retry_gateway(
        context,
        lambda request: httpx.Response(200, json=json.loads(CHAT_OK)),
        model_max_retries=0,
        model_timeout_seconds=0.0001,
    )
    try:
        with pytest.raises(AppError) as error:
            await gateway.json_chat("只回 JSON", "查询")
        assert error.value.code == 5002
        assert "TimeoutError" in error.value.message
        assert await context["app"].state.redis.zcard(gateway.prefix + "leases") == 0
    finally:
        await gateway.close()
