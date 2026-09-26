import json

import httpx
import pytest
from openai import AsyncOpenAI

from app.core.errors import AppError
from app.core.model_client import ModelGateway


def mock_gateway(context, handler):
    client = AsyncOpenAI(
        api_key="test-only",
        base_url="https://model.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return ModelGateway(
        context["settings"].model_copy(update={"model_max_retries": 0}), context["app"].state.redis, client
    )


async def test_embedding_response_order_and_dimension(context):
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "Qwen/Qwen3-Embedding-8B"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.5] * 4096, "object": "embedding"},
                    {"index": 0, "embedding": [1.0] * 4096, "object": "embedding"},
                ],
                "model": body["model"],
                "object": "list",
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            },
        )

    gateway = mock_gateway(context, handler)
    try:
        vectors = await gateway.embed(["Redis", "Kafka"])
        assert vectors[0][0] == 1
        assert vectors[1][0] == 0.5
    finally:
        await gateway.close()


@pytest.mark.parametrize(
    "data", [[{"index": 0, "embedding": [1.0] * 1024}], [{"index": 2, "embedding": [1.0] * 4096}]]
)
async def test_bad_embedding_is_rejected(context, data):
    gateway = mock_gateway(
        context,
        lambda request: httpx.Response(
            200,
            json={
                "data": data,
                "object": "list",
                "model": "test",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        ),
    )
    try:
        with pytest.raises(AppError) as error:
            await gateway.embed(["query"])
        assert error.value.code == 5002
    finally:
        await gateway.close()


@pytest.mark.parametrize(
    "status,hint",
    [
        (401, "模型服务凭据无效（401）"),
        (402, "模型服务账户余额不足，充值后再试（402）"),
        (403, "模型服务拒绝该凭据访问此模型（403）"),
        (429, "模型服务频率超限（429）"),
        (500, "模型服务不可用（500）"),
    ],
)
async def test_model_http_errors_do_not_leak_payloads(context, status, hint, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": {"message": "sensitive-upstream-body", "type": "upstream_error"}}
        )

    gateway = mock_gateway(context, handler)
    try:
        with pytest.raises(AppError) as error:
            await gateway.embed(["query"])
        assert error.value.status == 503
        assert "sensitive" not in error.value.message
        # 运维要能一眼看出下一步做什么（402 就是没钱），但上游原文只进服务端日志。
        assert error.value.message == hint
        assert len(calls) == 1
        assert await context["app"].state.redis.zcard(gateway.prefix + "leases") == 0
    finally:
        await gateway.close()
    recorded = [
        record.getMessage() for record in caplog.records if "model_call_failed" in record.getMessage()
    ]
    assert recorded and "sensitive-upstream-body" in recorded[0]


async def test_invalid_json_and_unconfigured_reranker(context):
    gateway = mock_gateway(
        context,
        lambda request: httpx.Response(
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
                        "message": {"role": "assistant", "content": "not JSON"},
                    }
                ],
            },
        ),
    )
    try:
        with pytest.raises(AppError):
            await gateway.json_chat("Return JSON", "query")
        gateway.settings = gateway.settings.model_copy(update={"model_rerank_path": ""})
        with pytest.raises(AppError) as error:
            await gateway.rerank("Redis", ["Redis caching"])
        assert "契约确认" in error.value.message
    finally:
        await gateway.close()


async def test_missing_model_configuration_fails_explicitly(context):
    gateway = ModelGateway(
        context["settings"].model_copy(update={"model_api_base_url": ""}), context["app"].state.redis
    )
    with pytest.raises(AppError) as error:
        await gateway.embed(["query"])
    assert "尚未配置" in error.value.message
