import json

import httpx
import pytest
from knowforge_sdk import AsyncKnowForge, KnowForgeError


async def test_sdk_sends_contract_and_preserves_uncalibrated_scores():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200, json={"code": 0, "data": {"results": [{"score": 0.032, "score_calibrated": False}]}}
        )

    async with AsyncKnowForge(
        "https://api.example.test/v1", "test-key", transport=httpx.MockTransport(handle)
    ) as sdk:
        result = await sdk.search("缓存穿透", filters={"tags": ["Redis"]}, options={"rerank": False})
        assert result["results"][0]["score"] == 0.032
        assert result["results"][0]["score_calibrated"] is False
        assert requests[0].url.path == "/v1/knowledge/search"
        assert requests[0].headers["Authorization"] == "Bearer test-key"
        assert json.loads(requests[0].content) == {
            "query": "缓存穿透",
            "search_type": "hybrid",
            "top_k": 8,
            "filters": {"tags": ["Redis"]},
            "options": {"rerank": False},
        }
        await sdk.lookup("doc_test", page_start=1, page_end=2, include_chunks=False)
        assert json.loads(requests[-1].content)["page_end"] == 2
        await sdk.tags("后端开发/缓存")
        assert requests[-1].url.params["category"] == "后端开发/缓存"
        await sdk.categories()
        assert requests[-1].url.path == "/v1/knowledge/categories"
    assert sdk._client.is_closed


@pytest.mark.parametrize("status,code", [(400, 1002), (401, 2001), (403, 2002), (429, 3001), (503, 5002)])
async def test_sdk_preserves_api_errors_without_retry(status, code):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={"code": code, "message": "请求失败"},
            headers={"X-Request-ID": "trace-test", "Retry-After": "60"},
        )

    async with AsyncKnowForge(
        "http://127.0.0.1:8000/v1", "test-key", transport=httpx.MockTransport(handle)
    ) as sdk:
        with pytest.raises(KnowForgeError) as error:
            await sdk.search("Redis")
        assert error.value.status == status
        assert error.value.code == code
        assert error.value.request_id == "trace-test"
        assert error.value.retry_after == "60"
        assert len(requests) == 1
        assert "test-key" not in str(error.value)


@pytest.mark.parametrize("payload", [[], {"code": False}, {"code": 0}, {"message": "missing code"}])
async def test_sdk_rejects_invalid_envelopes(payload):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with AsyncKnowForge("http://localhost/v1", "test-key", transport=transport) as sdk:
        with pytest.raises(KnowForgeError):
            await sdk.categories()


async def test_sdk_does_not_follow_redirects_or_allow_source_path_injection():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example.test/"})

    async with AsyncKnowForge(
        "https://api.example.test/v1", "test-key", transport=httpx.MockTransport(handle)
    ) as sdk:
        with pytest.raises(KnowForgeError) as error:
            await sdk.categories()
        assert error.value.status == 302
        assert len(requests) == 1
        for value in ["..", "../api-keys", "abc/def", "a?token=123", ""]:
            with pytest.raises(ValueError):
                await sdk.source(value)
        assert len(requests) == 1


async def test_sdk_download_and_timeout():
    async with AsyncKnowForge(
        "http://localhost/v1",
        "test-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"%PDF-1.7")),
    ) as sdk:
        assert await sdk.source("doc_sample") == b"%PDF-1.7"

    def timeout(request):
        raise httpx.ReadTimeout("private transport detail", request=request)

    async with AsyncKnowForge(
        "http://localhost/v1", "test-key", transport=httpx.MockTransport(timeout)
    ) as sdk:
        with pytest.raises(KnowForgeError, match="timed out") as error:
            await sdk.search("Redis")
        assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example/v1",
        "https://user:password@example.test/v1",
        "https://example.test/v1?key=secret",
        "https://example.test/v1#fragment",
    ],
)
def test_sdk_rejects_unsafe_base_url(url):
    with pytest.raises(ValueError):
        AsyncKnowForge(url, "test-key")
