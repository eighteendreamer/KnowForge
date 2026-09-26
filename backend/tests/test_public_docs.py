import json
import pathlib
import re

LANGUAGES = {"curl", "python", "javascript"}


async def test_public_api_docs_lists_only_open_endpoints_without_a_key(context):
    response = await context["client"].get("/v1/knowledge/api-docs")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    base_url = "http://test/v1/knowledge"
    assert data["base_url"] == base_url
    paths = {item["path"] for item in data["endpoints"]}
    assert paths == {
        "/v1/knowledge/api-docs",
        "/v1/knowledge/search",
        "/v1/knowledge/lookup",
        "/v1/knowledge/tags",
        "/v1/knowledge/categories",
        "/v1/knowledge/documents/{doc_id}/file",
    }
    assert "/admin" not in json.dumps(data, ensure_ascii=False)
    search_entry = next(item for item in data["endpoints"] if item["path"].endswith("/search"))
    assert search_entry["auth_required"] is True
    assert "四模式" in search_entry["summary"] or "检索" in search_entry["purpose"]
    assert {field["name"] for field in search_entry["request_fields"]} >= {"query", "search_type", "top_k"}
    assert next(f for f in search_entry["request_fields"] if f["name"] == "query")["required"] is True
    assert any(item["field"] == "score_calibrated" for item in search_entry["result_fields"])
    assert {item["field"] for item in search_entry["response_fields"]} >= {
        "search_type_used",
        "total",
        "results",
        "took_ms",
    }
    docs_entry = next(item for item in data["endpoints"] if item["path"].endswith("/api-docs"))
    assert docs_entry["auth_required"] is False


async def test_every_endpoint_documents_fields_in_three_languages(context):
    data = (await context["client"].get("/v1/knowledge/api-docs")).json()["data"]
    for item in data["endpoints"]:
        assert {example["language"] for example in item["examples"]} == LANGUAGES, item["path"]
        assert item["purpose"], item["path"]
        assert item["notes"], item["path"]


async def test_examples_point_at_the_documented_public_url(context):
    data = (await context["client"].get("/v1/knowledge/api-docs")).json()["data"]
    for item in data["endpoints"]:
        tail = item["path"].removeprefix("/v1/knowledge").strip("/").replace("{doc_id}", "doc_001")
        for example in item["examples"]:
            assert "/v1/v1/" not in example["code"], (item["path"], example["language"])
            if example["language"] == "curl":
                assert tail in example["code"], (item["path"], example["code"])
                assert "http://test/v1/knowledge" in example["code"], example["code"]


async def test_public_api_docs_can_filter_by_path(context):
    for needle, expected in {
        "tags": ["/v1/knowledge/tags"],
        "documents": ["/v1/knowledge/documents/{doc_id}/file"],
        "knowledge/search": ["/v1/knowledge/search"],
    }.items():
        response = await context["client"].get("/v1/knowledge/api-docs", params={"path": needle})
        assert response.status_code == 200
        assert [item["path"] for item in response.json()["data"]["endpoints"]] == expected, needle


async def test_error_codes_are_documented(context):
    data = (await context["client"].get("/v1/knowledge/api-docs")).json()["data"]
    codes = {item["code"] for item in data["error_codes"]}
    assert {0, 1001, 1002, 2001, 2002, 3001, 5001, 5002} <= codes


async def test_every_open_endpoint_documents_its_response_shape(context):
    """响应字段表必须逐个接口都在，否则文档页再怎么排版也变不出内容。"""
    data = (await context["client"].get("/v1/knowledge/api-docs")).json()["data"]
    for item in data["endpoints"]:
        assert item["response_fields"], (item["path"], "缺少响应字段表")
        assert all(row["meaning"] for row in item["response_fields"]), item["path"]
        examples = item["response_examples"]
        assert examples.get("success") and examples.get("failure"), item["path"]


async def test_docs_carry_version_rate_limits_quickstart_and_sdks(context):
    data = (await context["client"].get("/v1/knowledge/api-docs")).json()["data"]
    assert data["version"] == "0.1.0"
    assert data["changelog"] and all(entry["changes"] for entry in data["changelog"])
    counters = {row["name"] for row in data["rate_limits"]["counters"]}
    assert counters == {"rate_limit_per_minute", "rate_limit_per_day"}
    assert data["rate_limits"]["on_exceed"]["header"] == "Retry-After（秒）"
    steps = [row["step"] for row in data["quickstart"]]
    assert steps == sorted(steps) and steps[0] == 1
    packages = {row["package"] for row in data["sdks"]}
    assert packages == {"knowforge-sdk", "@knowforge/sdk"}


async def test_performance_numbers_ship_with_measurement_conditions(context):
    """容量数字必须带测量条件一起下发，落地页与文档页共用这一份，不各写一套。"""
    data = (await context["client"].get("/v1/knowledge/api-docs")).json()["data"]
    assert "连接池" in data["performance"]["measure"]
    assert {row["scenario"] for row in data["performance"]["throughput"]} >= {
        "缓存命中检索（含精排）",
        "冷查询（关闭改写与精排）",
        "元数据接口（tags / categories）",
    }
    assert all(row["p95"] and row["note"] for row in data["performance"]["throughput"])
    assert data["performance"]["latency"] and data["performance"]["bottleneck"]


def sdk_sources() -> dict[str, str]:
    root = pathlib.Path(__file__).resolve().parents[2]
    return {
        "python": (root / "sdk/python/knowforge_sdk/__init__.py").read_text(encoding="utf-8"),
        "javascript": (root / "sdk/javascript/index.js").read_text(encoding="utf-8"),
    }


async def test_documented_sdk_methods_exist_on_the_shipped_clients(context):
    """文档里写的客户端方法必须真在 sdk/ 里存在，否则文档与代码会各走各路。"""
    sources = sdk_sources()
    data = (await context["client"].get("/v1/knowledge/api-docs")).json()["data"]
    for sdk in data["sdks"]:
        source = sources[sdk["language"]]
        for method in sdk["methods"]:
            name = re.match(r"([a-z_]+)\(", method).group(1)
            assert re.search(rf"\b{name}\s*\(", source), (sdk["language"], method)


def test_search_result_fields_match_what_the_service_returns():
    """响应字段表要贴着实现：service 改了返回键，这条测试先红。"""
    import app.api.public_docs as docs

    source = (
        pathlib.Path(docs.__file__).resolve().parents[1] / "services/retrieval/search_service.py"
    ).read_text(encoding="utf-8")
    envelope = re.search(r"data = \{(.*?)\n    \}", source, re.S).group(1)
    documented = {row["field"] for row in docs.SEARCH_DATA_FIELDS}
    assert set(re.findall(r'"([a-z_]+)":', envelope)) <= documented
    assert {"search_type_used", "results", "took_ms"} <= documented
