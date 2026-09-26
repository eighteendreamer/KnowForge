"""Curated documentation for the public knowledge API, merged with the live OpenAPI schema."""

from typing import Any

PUBLIC_PREFIX = "/v1/knowledge"

ERROR_CODES = [
    {"code": 0, "http": 200, "meaning": "成功，业务数据在 data 字段"},
    {"code": 1001, "http": 400, "meaning": "参数缺失或格式错误、对象不存在或不可访问"},
    {"code": 1002, "http": 400, "meaning": "query 为空或超过 500 字"},
    {"code": 2001, "http": 401, "meaning": "缺少、无效或已过期、已吊销的 API Key"},
    {"code": 2002, "http": 403, "meaning": "API Key 缺少 knowledge:read 作用域"},
    {"code": 3001, "http": 429, "meaning": "超过每分钟或每日配额，按 Retry-After 响应头退避"},
    {"code": 5001, "http": 500, "meaning": "服务器内部错误，请携带 X-Request-ID 报障"},
    {
        "code": 5002,
        "http": 503,
        "meaning": "远程模型不可用或响应不合契约，可降级到 keyword/fuzzy；message 会给出可行动的原因（如账户余额不足 402、凭据无效 401、频率超限 429），上游原文只留在服务端日志",
    },
]

SEARCH_RESULT_FIELDS = [
    {"field": "id", "type": "string", "meaning": "分块对外标识 chunk_*"},
    {
        "field": "merged_ids",
        "type": "string[]|缺失",
        "meaning": "该条并入了哪些同文档相邻分块（仅 options.merge_adjacent 且发生合并时出现）",
    },
    {"field": "doc_id", "type": "string", "meaning": "文档对外标识 doc_*，用于 lookup 与原文下载"},
    {"field": "title", "type": "string", "meaning": "文档标题"},
    {"field": "content", "type": "string", "meaning": "分块正文，Markdown 文本"},
    {
        "field": "highlight",
        "type": "string|null",
        "meaning": "命中词高亮片段（已转义，仅 options.highlight）",
    },
    {"field": "score", "type": "number", "meaning": "排序分数，含义由 score_type 决定，不可跨类型比较"},
    {"field": "score_type", "type": "string", "meaning": "rrf / semantic / keyword / fuzzy / rerank"},
    {
        "field": "score_calibrated",
        "type": "boolean",
        "meaning": "分数是否已按评估集校准；false 时不要自建阈值",
    },
    {"field": "source", "type": "string", "meaning": "原始文件名，用于来源引用"},
    {"field": "page", "type": "integer|null", "meaning": "起始页码；HTML 文档为 null"},
    {"field": "section", "type": "string", "meaning": "父标题链，以 > 分隔（需 include_metadata）"},
    {"field": "tags", "type": "string[]", "meaning": "该分块已审核通过的标签"},
    {"field": "category", "type": "string", "meaning": "生效分类路径"},
    {"field": "difficulty", "type": "string|null", "meaning": "初级 / 中级 / 高级"},
    {"field": "url", "type": "string", "meaning": "原文下载地址（需 include_metadata）"},
]

SEARCH_DATA_FIELDS = [
    {"field": "query", "type": "string", "meaning": "回显调用方传入的原始 query"},
    {
        "field": "query_rewritten",
        "type": "string|null",
        "meaning": "改写后的查询；与繁简归一结果一致时为 null，说明没有发生改写",
    },
    {
        "field": "search_type_used",
        "type": "string",
        "meaning": "实际生效的检索模式；search_type=auto 时这里才是落地模式",
    },
    {
        "field": "total",
        "type": "integer",
        "meaning": "合并相邻分块之前的分块级候选数，因此可以大于 results 条数",
    },
    {"field": "results", "type": "object[]", "meaning": "命中列表，字段见 results 表"},
    {
        "field": "suggested_tags",
        "type": "string[]",
        "meaning": "本次命中里出现过的已过审标签，最多 10 个，已用于过滤的标签会被剔除",
    },
    {
        "field": "took_ms",
        "type": "integer",
        "meaning": "服务端处理耗时，不含网络往返；缓存命中时只计缓存之后的部分",
    },
]

LOOKUP_FIELDS = [
    {"field": "doc_id", "type": "string", "meaning": "文档对外标识 doc_*"},
    {"field": "title", "type": "string", "meaning": "文档标题"},
    {"field": "source", "type": "string", "meaning": "原始文件名，用于来源引用"},
    {
        "field": "content",
        "type": "string",
        "meaning": "按页码过滤后的正文元素纯文本，以空行拼接，可直接渲染",
    },
    {
        "field": "elements",
        "type": "object[]",
        "meaning": "正文元素：type(NarrativeText|Heading|Table|ListItem|Code)、text、level(仅 Heading)、page、char_count",
    },
    {
        "field": "chunks",
        "type": "object[]|缺失",
        "meaning": "include_chunks=true 时才有：id、content、page_start、page_end，按 chunk_index 排序",
    },
]

TAGS_FIELDS = [
    {
        "field": "tags",
        "type": "object[]",
        "meaning": "每项含 name、count（挂载该标签的可见文档数）、category（回显查询参数，未传为 null）",
    }
]

CATEGORIES_FIELDS = [
    {
        "field": "tree",
        "type": "object[]",
        "meaning": "根节点数组，每项 name、path、count（含子孙的可见文档数）、children（同结构递归，叶子为空数组）",
    }
]

FILE_FIELDS = [
    {
        "field": "body",
        "type": "application/octet-stream",
        "meaning": "原始 PDF 或 HTML 字节流，不按 JSON 信封返回；出错时才回 JSON 错误体",
    },
    {
        "field": "Content-Disposition",
        "type": "header",
        "meaning": "attachment; filename=<原始文件名>",
    },
    {"field": "X-Request-ID", "type": "header", "meaning": "所有响应都带，报障时提供该值"},
]

API_DOCS_FIELDS = [
    {"field": "base_url", "type": "string", "meaning": "由当前请求地址推导的接入根地址"},
    {"field": "auth", "type": "string", "meaning": "鉴权说明"},
    {"field": "envelope", "type": "object", "meaning": "成功与失败的响应结构描述"},
    {"field": "error_codes", "type": "object[]", "meaning": "错误码表"},
    {"field": "rate_limits", "type": "object", "meaning": "配额与退避规则"},
    {"field": "performance", "type": "object", "meaning": "实测吞吐、延迟与瓶颈，含测量条件"},
    {"field": "quickstart", "type": "object[]", "meaning": "从建钥到引用的上手步骤"},
    {"field": "sdks", "type": "object[]", "meaning": "官方客户端包名与方法面"},
    {"field": "version", "type": "string", "meaning": "服务版本，取自 OpenAPI info.version"},
    {"field": "changelog", "type": "object[]", "meaning": "本文档的变更记录"},
    {"field": "endpoints", "type": "object[]", "meaning": "接口清单，可用 ?path=search 过滤"},
]

RESPONSE_FIELDS: dict[str, list[dict[str, str]]] = {
    "/v1/knowledge/search": SEARCH_DATA_FIELDS,
    "/v1/knowledge/lookup": LOOKUP_FIELDS,
    "/v1/knowledge/tags": TAGS_FIELDS,
    "/v1/knowledge/categories": CATEGORIES_FIELDS,
    "/v1/knowledge/documents/{doc_id}/file": FILE_FIELDS,
    "/v1/knowledge/api-docs": API_DOCS_FIELDS,
}

RESPONSE_EXAMPLES: dict[str, dict[str, str]] = {
    "/v1/knowledge/search": {
        "success": (
            '{"code":0,"message":"success","data":{"query":"Redis 缓存穿透如何解决","query_rewritten":null,'
            '"search_type_used":"hybrid","total":24,"results":[{"id":"chunk_3b44...","doc_id":"doc_df0c...",'
            '"title":"Redis 设计与实现","content":"...","score":0.0328,"score_type":"rrf",'
            '"score_calibrated":false,"source":"redis.pdf","page":137}],'
            '"suggested_tags":["Redis","缓存"],"took_ms":28}}'
        ),
        "failure": '{"code":3001,"message":"超过调用频率或日配额限制"}  // 响应头含 Retry-After: 12',
    },
    "/v1/knowledge/lookup": {
        "success": (
            '{"code":0,"message":"success","data":{"doc_id":"doc_df0c...","title":"Redis 设计与实现",'
            '"source":"redis.pdf","content":"缓存穿透是指查询一定不存在的数据...\\n\\n...",'
            '"elements":[{"type":"NarrativeText","text":"缓存穿透是指...","page":137,"char_count":64}],'
            '"chunks":[{"id":"chunk_3b44...","content":"...","page_start":137,"page_end":138}]}}'
        ),
        "failure": '{"code":1001,"message":"文档不存在或不可访问"}  // 非 ready 或非公开文档按 404 处理',
    },
    "/v1/knowledge/tags": {
        "success": (
            '{"code":0,"message":"success","data":{"tags":[{"name":"Redis","count":18,"category":"后端开发/缓存"},'
            '{"name":"缓存穿透","count":4,"category":"后端开发/缓存"}]}}'
        ),
        "failure": '{"code":2001,"message":"API Key 无效或已过期"}',
    },
    "/v1/knowledge/categories": {
        "success": (
            '{"code":0,"message":"success","data":{"tree":[{"name":"后端开发","path":"后端开发","count":96,'
            '"children":[{"name":"缓存","path":"后端开发/缓存","count":41,"children":[]}]}]}}'
        ),
        "failure": '{"code":2002,"message":"API Key 无检索权限"}',
    },
    "/v1/knowledge/documents/{doc_id}/file": {
        "success": "HTTP/1.1 200 OK\\r\\nContent-Type: application/octet-stream\\r\\nContent-Disposition: "
        'attachment; filename="redis.pdf"\\r\\nX-Request-ID: 7f0c...\\r\\n\\r\\n<二进制正文>',
        "failure": '{"code":1001,"message":"文档不存在或不可访问"}',
    },
    "/v1/knowledge/api-docs": {
        "success": (
            '{"code":0,"message":"success","data":{"base_url":"http://127.0.0.1:8000/v1/knowledge",'
            '"auth":"Authorization: Bearer <API Key>，作用域 knowledge:read","version":"0.1.0",'
            '"endpoints":[{"method":"POST","path":"/v1/knowledge/search",...}]}}'
        ),
        "failure": '{"code":1001,"message":"参数缺失或格式错误"}  // path 参数超长时',
    },
}

RATE_LIMITS = {
    "scope": "按 API Key 计量，不是按 IP；同一账号下多把密钥各自独立计数。",
    "counters": [
        {
            "name": "rate_limit_per_minute",
            "default": 60,
            "unit": "次/分钟",
            "meaning": "令牌桶容量，桶按容量/60 每秒回填，突发可打满一桶。",
        },
        {
            "name": "rate_limit_per_day",
            "default": 1000,
            "unit": "次/自然日(UTC)",
            "meaning": "每日累计上限，跨日自动重置。",
        },
    ],
    "on_exceed": {
        "http": 429,
        "code": 3001,
        "header": "Retry-After（秒）",
        "client_rule": "按 Retry-After 整秒退避后重试一次；禁止并发重试，那只会把配额耗尽得更早。",
    },
    "shared_budget": (
        "在线检索与离线批量任务（全量重建、评估标注）共用同一个模型调用频率桶 model_requests_per_minute，"
        "重建期间出现 429 属预期，退避即可，不代表服务故障。"
    ),
    "quota_note": "配额是频控不是计费：调用被拒只因为超频，与账户余额无关。",
}

QUICKSTART = [
    {
        "step": 1,
        "title": "创建 API Key",
        "detail": (
            "门户控制台「API Key」页创建，明文只显示一次，请立即保存。服务端只存 SHA-256 摘要，丢失只能重建。"
        ),
    },
    {
        "step": 2,
        "title": "发出第一条请求",
        "detail": (
            "带上 Authorization: Bearer <key> 调用 POST {base_url}/search，先用 search_type=keyword 验证连通，"
            "再切到 hybrid。keyword 不调用向量模型，最快也最便宜。"
        ),
    },
    {
        "step": 3,
        "title": "读响应而不是猜响应",
        "detail": (
            "以 search_type_used 判断实际生效模式，以 took_ms 判断是否命中缓存，"
            "用 total 与 results 条数的差理解相邻分块合并。"
        ),
    },
    {
        "step": 4,
        "title": "把结论落到来源上",
        "detail": (
            "引用 results[].source + page + section；需要核验原文时按 doc_id 调 lookup 或直接下载 "
            "documents/{doc_id}/file。score 只有同一 search_type 内可比，score_calibrated=false 时不要自建阈值。"
        ),
    },
    {
        "step": 5,
        "title": "空结果按未覆盖处理",
        "detail": (
            "results 为空数组表示知识库没有依据，应答“知识库未覆盖”。不要补写内容，也不要放宽到无过滤重查。"
        ),
    },
]

SDKS = [
    {
        "language": "python",
        "package": "knowforge-sdk",
        "install": "pip install knowforge-sdk",
        "requires": "Python >= 3.11，依赖 httpx",
        "client": "AsyncKnowForge(base_url, api_key)",
        "methods": [
            "search(query, *, search_type='hybrid', top_k=8, filters=None, options=None)",
            "lookup(doc_id, *, page_start=None, page_end=None, include_chunks=True)",
            "tags(category=None)",
            "categories()",
            "source(doc_id) -> bytes",
        ],
        "note": "异步上下文管理器用法：async with AsyncKnowForge(base_url, key) as kf。返回的是信封里的 data。",
    },
    {
        "language": "javascript",
        "package": "@knowforge/sdk",
        "install": "npm install @knowforge/sdk",
        "requires": "Node >= 22，使用内置 fetch，仅服务端保存 API Key",
        "client": "new KnowForge({ baseUrl, apiKey, timeout = 120000 })",
        "methods": [
            "search(query, { search_type='hybrid', top_k=8, filters={}, options={}, signal })",
            "lookup(doc_id, { page_start=null, page_end=null, include_chunks=true, signal })",
            "tags(category, { signal })",
            "categories({ signal })",
            "source(docId, { signal })",
        ],
        "note": "抛 KnowForgeError，携带 status、code、requestId、retryAfter，可直接据此退避。",
    },
]

PERFORMANCE = {
    "measure": (
        "单节点实测：语料 2494 分块，数据库连接池 20+40，统计的是不调用远程模型的路径。"
        "承诺吞吐请按这组数字规划，不要按 1000 QPS 规划。"
    ),
    "throughput": [
        {
            "scenario": "缓存命中检索（含精排）",
            "sustainable": "60 req/s",
            "p95": "62 ms",
            "note": "命中率 1.0、0 错误；120 req/s 起约 36% 请求返回 500。",
        },
        {
            "scenario": "冷查询（关闭改写与精排）",
            "sustainable": "40 req/s",
            "p95": "2.8 s",
            "note": "0 错误，耗时主要是向量与召回。",
        },
        {
            "scenario": "元数据接口（tags / categories）",
            "sustainable": "50 req/s",
            "p95": "29 ms",
            "note": "不触碰模型，也不写缓存。",
        },
    ],
    "latency": [
        {
            "scenario": "keyword 检索 + 精排",
            "value": "1424 ms",
            "note": "精排是一次远程模型调用，占了绝大部分耗时。",
        },
        {
            "scenario": "keyword 检索，options.rerank=false",
            "value": "91 ms",
            "note": "服务端 took_ms 约 28 ms，其余是网络与序列化。",
        },
        {"scenario": "semantic 检索，不带精排", "value": "352 ms", "note": "主要是查询向量。"},
    ],
    "bottleneck": (
        "默认连接池 10+20 时，缓存命中 60 req/s 的 p95 会退化到 643 ms、冷查询只能 20 req/s，"
        "瓶颈是连接池与 5 秒 pool_timeout；调到 20+40 后剩下的上限来自单个 API 进程的事件循环，"
        "要更高吞吐需要多副本。"
    ),
}

CHANGELOG = [
    {
        "date": "2026-09-26",
        "changes": [
            "文档补齐 lookup / tags / categories / 原文下载 / api-docs 自身的响应字段表与成功、失败响应示例。",
            "新增 rate_limits、quickstart、sdks、version、changelog 四个章节，限流语义不再散落在接口备注里。",
            "对外检索接口本身的行为未变：仍是 search / lookup / tags / categories / documents/{doc_id}/file 五个。",
        ],
    }
]

GUIDE = {
    ("post", "/v1/knowledge/search"): {
        "summary": "四模式知识检索",
        "purpose": (
            "在已入库且状态为 ready 的公开文档中检索分块。semantic 用 Dense 向量做语义匹配；"
            "keyword 用 BM25 稀疏向量做关键词召回；fuzzy 用 PostgreSQL pg_trgm 做字面近似（抗拼写误差）；"
            "hybrid 三路召回后 RRF 融合，并可叠加 Rerank 精排。返回结果自带来源文件名与页码，可直接引用。"
        ),
        "notes": [
            "query 1~500 字，纯空白会被拒绝（1002）。",
            "查询侧先做繁简归一并剔除停用词，再按开关改写；文档侧词形保持入库原样，所以繁体提问也能命中简体语料，而 query_rewritten 只反映 LLM 改写的差异。",
            "options.merge_adjacent 默认 true：同一文档、序号相邻的命中会并成一条（重叠区去重后拼接，并进来的分块列在 merged_ids）。此时 top_k 数的是合并后的条数，total 仍是合并前的分块级候选数；需要逐分块结果时传 false。",
            "query_rewrite 仅对 semantic 与 hybrid 生效；服务端总开关关闭精排时 options.rerank 会被忽略。",
            "filters.category 按分类树展开子树，不做字符串前缀猜测；filters.tags 只接受已审核通过的标签。",
            "results 为空数组表示未命中，此时应回答“知识库未覆盖”，不要补写内容。",
            "精排默认开启且是一次远程模型调用，对延迟敏感的调用方可以显式传 options.rerank=false；具体毫秒与吞吐见「性能与容量」章节，那里是唯一数字来源。",
            "search_type=auto 由服务端按意图在 keyword 与 hybrid 之间选：以英文术语/标识符为主体的走 keyword（词面命中优先且不调用向量），叙述或提问走 hybrid（默认推荐）；fuzzy 不参与自动选择，因为无词表参照就无法判断拼写不确定。响应里的 search_type_used 是最终生效模式，调用日志记的也是它。实测这份 275 条冻结集里 271 条判为叙述型走 hybrid、4 条术语型走 keyword。",
            "承诺吞吐按「性能与容量」章节的实测数字规划，不要按 1000 QPS 规划；走模型的路径比命中缓存的路径慢一个数量级，两者不能共用同一个 SLO。",
            "在线检索与离线批量任务共用同一个模型频率桶（model_requests_per_minute）：全量重建或评估标注跑起来时可能返回 429（3001），按 Retry-After 秒数退避重试，不要并发重试放大。",
        ],
    },
    ("post", "/v1/knowledge/lookup"): {
        "summary": "按文档取回原文与分块",
        "purpose": (
            "把检索结果对齐回原文：按 doc_id 取回文档标题、来源文件名、正文元素与分块边界，"
            "可用 page_start/page_end 限定页码范围。用于引用核验和上下文扩写。"
        ),
        "notes": [
            "只能访问 ready 且公开的文档，否则 404（1001）。",
            "page_end 必须与 page_start 同时给出且不小于起始页。",
            "include_chunks=false 时只返回正文元素，适合渲染整页内容。",
        ],
    },
    ("get", "/v1/knowledge/tags"): {
        "summary": "列出可用标签及命中量",
        "purpose": (
            "返回已审核通过、且确实挂在可见文档上的标签及文档数，供检索前收窄范围。"
            "待审核标签不会出现，因此可安全用作 filters.tags 的取值来源。"
        ),
        "notes": ["可选 category 参数按分类子树过滤；返回项含 count 与所属 category。"],
    },
    ("get", "/v1/knowledge/categories"): {
        "summary": "获取分类树与文档计数",
        "purpose": "返回完整分类树（含每个节点的可见文档数），用于按领域逐层下钻检索。",
        "notes": ["count 统计该节点及其子孙的可见文档；父节点计数包含子节点。"],
    },
    ("get", "/v1/knowledge/documents/{doc_id}/file"): {
        "summary": "下载原始文件",
        "purpose": "取回检索结果对应的原始 PDF 或 HTML 字节流，作为可核验的一手来源。",
        "notes": [
            "响应为二进制流（application/octet-stream），不是 JSON 信封。",
            "仅 ready 且公开的文档可见。",
        ],
    },
    ("get", "/v1/knowledge/api-docs"): {
        "summary": "查询对外开放的接口清单",
        "purpose": (
            "返回本知识库对外开放的全部接口：能力说明、参数与响应字段、错误码和 curl/Python/JavaScript 示例。"
            "该接口不需要 API Key，供接入方自检；管理端接口不会出现在这里。"
        ),
        "notes": ["path 参数可按接口路径过滤，例如 ?path=search。"],
    },
}


def examples(base_url: str, method: str, path: str) -> list[dict[str, str]]:
    # base_url already carries the /v1/knowledge root, so examples append only the route tail.
    url = base_url + path.removeprefix(PUBLIC_PREFIX)
    key = (method.lower(), path)
    if key == ("post", "/v1/knowledge/search"):
        payload = (
            '{"query":"Redis 缓存穿透如何解决","search_type":"hybrid","top_k":5,'
            '"filters":{"tags":["Redis"],"category":"后端开发/缓存"},"options":{"rerank":true}}'
        )
        return [
            {
                "language": "curl",
                "code": f'curl -sS {url} -H "Authorization: Bearer $KF_KEY" '
                f"-H 'Content-Type: application/json' -d '{payload}'",
            },
            {
                "language": "python",
                "code": "import asyncio\nfrom knowforge_sdk import AsyncKnowForge\n\n"
                "async def main():\n"
                f"    async with AsyncKnowForge('{base_url}', api_key) as kf:\n"
                "        data = await kf.search('Redis 缓存穿透如何解决', search_type='hybrid', top_k=5,\n"
                "                              filters={'category': '后端开发/缓存'})\n"
                "        for item in data['results']:\n"
                "            print(item['score'], item['source'], item['page'], item['content'][:80])\n\n"
                "asyncio.run(main())",
            },
            {
                "language": "javascript",
                "code": "import { KnowForge } from '@knowforge/sdk'\n\n"
                f"const kf = new KnowForge({{ baseUrl: '{base_url}', apiKey: process.env.KF_KEY }})\n"
                "const data = await kf.search('Redis 缓存穿透如何解决', { search_type: 'hybrid', top_k: 5,\n"
                "  filters: { category: '后端开发/缓存' } })\n"
                "for (const item of data.results) console.log(item.score, item.source, item.page)",
            },
        ]
    if key == ("post", "/v1/knowledge/lookup"):
        payload = '{"doc_id":"doc_001","page_start":1,"page_end":2,"include_chunks":true}'
        return [
            {
                "language": "curl",
                "code": f'curl -sS {url} -H "Authorization: Bearer $KF_KEY" '
                f"-H 'Content-Type: application/json' -d '{payload}'",
            },
            {
                "language": "python",
                "code": "detail = await kf.lookup('doc_001', page_start=1, page_end=2)\n"
                "print(detail['title'], detail['source'])\n"
                "for chunk in detail['chunks']:\n    print(chunk['id'], chunk['page_start'], chunk['text'][:60])",
            },
            {
                "language": "javascript",
                "code": "const detail = await kf.lookup('doc_001', { page_start: 1, page_end: 2 })\n"
                "console.log(detail.data.title, detail.data.chunks.length)",
            },
        ]
    if key == ("get", "/v1/knowledge/tags"):
        return [
            {
                "language": "curl",
                "code": f'curl -sS "{url}?category=后端开发/缓存" -H "Authorization: Bearer $KF_KEY"',
            },
            {
                "language": "python",
                "code": "data = await kf.tags(category='后端开发/缓存')\nprint(data['tags'])",
            },
            {
                "language": "javascript",
                "code": f"const {{ data }} = await fetch('{url}?category=后端开发/缓存', "
                "{ headers: { Authorization: `Bearer ${process.env.KF_KEY}` } }).then((r) => r.json())\n"
                "console.log(data.tags.map((tag) => `${tag.name}:${tag.count}`))",
            },
        ]
    if key == ("get", "/v1/knowledge/categories"):
        return [
            {"language": "curl", "code": f'curl -sS {url} -H "Authorization: Bearer $KF_KEY"'},
            {"language": "python", "code": "data = await kf.categories()\nprint(data['tree'])"},
            {
                "language": "javascript",
                "code": f"const {{ data }} = await fetch('{url}',\n"
                "  { headers: { Authorization: `Bearer ${process.env.KF_KEY}` } }).then((r) => r.json())\n"
                "console.log(data.tree.map((node) => node.path))",
            },
        ]
    if key == ("get", "/v1/knowledge/documents/{doc_id}/file"):
        return [
            {
                "language": "curl",
                "code": f"curl -sS -o source.pdf {base_url}/documents/doc_001/file "
                '-H "Authorization: Bearer $KF_KEY"',
            },
            {
                "language": "python",
                "code": "raw = await kf.source('doc_001')\nopen('source.pdf','wb').write(raw)",
            },
            {
                "language": "javascript",
                "code": "const bytes = await kf.source('doc_001')\nawait writeFile('source.pdf', bytes)",
            },
        ]
    return [
        {"language": "curl", "code": f"curl -sS {url}"},
        {
            "language": "python",
            "code": f"import httpx\n\nresponse = httpx.get('{url}')\n"
            "for item in response.json()['data']['endpoints']:\n"
            "    print(item['method'], item['path'], item['summary'])",
        },
        {
            "language": "javascript",
            "code": f"const docs = await fetch('{url}').then((response) => response.json())\n"
            "console.log(docs.data.endpoints.map((item) => `${item.method} ${item.path}`))",
        },
    ]


def public_endpoints(schema: dict[str, Any], base_url: str) -> list[dict[str, Any]]:
    components = schema.get("components", {}).get("schemas", {})
    endpoints = []
    for path, operations in sorted(schema.get("paths", {}).items()):
        if not path.startswith(PUBLIC_PREFIX):
            continue
        for method, operation in operations.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            guide = GUIDE.get((method, path), {})
            body_schema = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            resolved = resolve_schema(body_schema.get("$ref", ""), components) if body_schema else {}
            endpoints.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "summary": guide.get("summary", operation.get("summary", "")),
                    "purpose": guide.get("purpose", operation.get("description", "")),
                    "notes": guide.get("notes", []),
                    "auth_required": path != f"{PUBLIC_PREFIX}/api-docs",
                    "request_fields": json_schema_fields(resolved, components),
                    "response_fields": RESPONSE_FIELDS.get(path, []),
                    "result_fields": SEARCH_RESULT_FIELDS if path.endswith("/search") else [],
                    "response_examples": RESPONSE_EXAMPLES.get(path, {}),
                    "examples": examples(base_url, method, path),
                    "responses": sorted({str(code) for code in operation.get("responses", {})}),
                }
            )
    return endpoints


def resolve_schema(reference: str, components: dict[str, Any]) -> dict[str, Any]:
    name = reference.rsplit("/", 1)[-1]
    return components.get(name, {})


def json_schema_fields(schema: dict[str, Any], components: dict[str, Any]) -> list[dict[str, Any]]:
    fields = []
    required = set(schema.get("required", []))
    for name, spec in schema.get("properties", {}).items():
        target = spec
        if "$ref" in target:
            target = resolve_schema(target["$ref"], components)
        kind = target.get("type") or ("object" if "properties" in target else "any")
        if target.get("enum"):
            kind = " | ".join(str(item) for item in target["enum"])
        fields.append(
            {
                "name": name,
                "type": kind,
                "required": name in required,
                "default": target.get("default"),
                "description": target.get("description", ""),
            }
        )
    return fields
