# 仓库规范

## 最高优先级铁律

1. 先查证，再动手。任何命令、依赖、配置、框架行为，都先确认来源。
2. 禁止臆造。没有在仓库里看到的结构、脚本、版本，不要当成既有事实。
3. 改动要小而完整。优先修当前目标，不顺手扩范围。
4. 完成以验证为准。没跑过相关检查、测试或手动验证，不算完成。
5. 忠实执行用户意图。不要擅自重构无关代码。

## 项目结构

- `backend/app/`：FastAPI 应用；路由放 `api/routes/`，公共配置放 `core/`，服务逻辑放 `services/`。
- `backend/tests/`：pytest 测试，与后端行为对应。
- `frontend/src/`：Vue 3 + TypeScript 管理端；组件测试放 `src/tests/`。
- `frontend-portal/src/`：Vue 3 + TypeScript 用户端门户（欢迎页、注册登录、控制台、API Key、接口文档、充值预留）；独立工程、独立 `package.json`，组件测试放 `src/tests/`。
- `data/storage/`：本地文件存储，仅提交 `.gitkeep`。
- `scripts/`：Windows 开发、检查和容器启停脚本。
- `docs/`：开发计划与设计记录；`docs-spider/` 保存资料采集工具和素材。

## 证据优先级

动手前先按顺序核对：

1. 当前仓库已有实现
2. README、脚本、配置文件
3. 用户明确给出的参考仓库或文档
4. 通用官方文档

如果没有依据，就明确说明这是本地约定，不要假装已有规范。

## 开发规范

- 保持改动贴近现有代码风格，不要混用多套格式。
- 优先使用仓库已有工具链；如果要新增 formatter、lint 或 build 脚本，要同步写进本文件。
- 命名保持直白一致：目录和文件名尽量小写、语义清晰；测试名直接表达行为。
- 不要提交密钥、Token、机器专属配置或敏感日志。
- 远程调用（模型、Qdrant）之前必须用 `await session.commit()` 结束只读事务，禁止跨远程调用持有池化连接；需要释放时用 `commit()` 而不是 `rollback()`，`rollback()` 会让依赖里已加载的 ORM 对象失效。
- 数据库连接池在 `.env` 与 `.env.example` 里显式钉成 `KNOFORGE_DB_POOL_SIZE=20` + `KNOFORGE_DB_MAX_OVERFLOW=40`，高于代码默认 10+20。依据是实测：默认 30 个连接、`pool_timeout=5s` 时缓存命中检索 60 req/s 的 p95 是 643ms、冷查询只能 20 req/s，失败都是 `QueuePool limit ... reached`；调到 60 后同一速率 p95 62ms、冷查询 40 req/s。改这两个值必须重跑 `scripts/acceptance_load.py` 的速率阶梯，并同步《运行与恢复手册》6.4 与 `GET /v1/knowledge/api-docs` 的容量说明。
- 请求处理函数里禁止在事件循环上跑 CPU 重活：HTML 解码净化、文件读写走 `asyncio.to_thread`，jieba 词典这类懒加载开销在 `lifespan` 启动时预热。回归测试 `test_html_preview_keeps_the_event_loop_responsive` 用一个心跳计数器盯这件事。
- 验收数字必须真的量到声明的东西，报告里要能自证：取值前先确认统计口径（标注必须是池化的、切换门必须只认生产变体、标签准确率必须抽全部 AI 关联而不是已过审的少数）；`thresholds_passed` 之类的判定必须先确认指标读到了值，k6 的数值在 metric 的 `values` 里、`http_req_failed` 的比率字段叫 `rate`，取错层级会得到 `null` 再被 `or 0` 判成通过。压测的 `cached` 场景定义是"命中缓存、不调用模型"，预热失败的模式要从请求集合里剔除并记录原因，不能把 miss 当 hit 报。
- 前端展示分块与文档正文一律走 `components/MarkdownBlock.vue`（`markdown-it` + `DOMPurify`，命中词用 TreeWalker 打 `<mark>`），不要把正文当纯文本拼接渲染。管理端与门户是两个独立工程，各持一份该文件，改渲染规则要同步两处。
- 任何"入队让 worker 干活"的提交都必须先证明有消费者（`task_queue.require_consumer`：应答 ping 或有 120 秒内更新过的 `running` 任务/重建），并在落盘、建行之前拒绝。`send_task` 只要 Redis 收下就成功，返回 advisory 布尔（如旧的 `queued: true`）等于把"没人干活"伪装成成功；solo 池 worker 跑长任务时答不了 ping，只按 ping 判活会误挡健康实例，所以兜底那条路不能省。
- 门指标只允许一处定义：`backend/app/schemas/system.py` 的 `EVALUATION_TARGETS`，服务端与 `scripts/acceptance_evaluation.py` 都导入它，`test_evaluation_targets.py` 钉住键与 `EvaluationMetrics` 字段对齐。定指标要先验证它可达——池化标注下未打分一律按不相关计，Recall@K 有 `min(K, 相关数)/相关数` 的上界（冻结集实测 @5 上界 0.7792），不要拿一个数学上不可能过的数当门。
- `tag_auto_approve_confidence` 不得高于 `app.core.config.LLM_ONLY_CONFIDENCE`（该字段用 `le=` 钉住，写高了在 `Settings` 校验阶段就抛错）。高于会导致每个 LLM 标签永久 pending，而 Qdrant payload、`filters.tags`、检索响应都只认已过审标签，标签功能会在无人察觉的情况下整体失效。改这个值的生效路径是 `PUT /v1/admin/system/settings`（运行时配置覆盖代码默认值），只改 `config.py` 对已部署的库无效；历史库里被写错成 0.9 的行由 `a3d9c51e7b48_repair_tag_auto_approve_confidence` 修回。
- 对外开放接口清单由 `GET /v1/knowledge/api-docs` 从实时 OpenAPI 过滤生成，新增 `/v1/knowledge/*` 接口只要在 `api/public_docs.py` 补条目，不要在别处复制一份接口说明。实测容量同理：数字只存在 `public_docs.py` 的 `PERFORMANCE` 里，欢迎页文案不许抄写任何毫秒数与 req/s。
- 给用户的过滤项必须来自服务端元数据接口：标签用 `GET /v1/knowledge/tags?category=`（只返回已过审标签，带 count），分类用 `GET /v1/knowledge/categories`。自由输入标签或分类路径不是"更灵活"，而是把拼错变成静默空结果——`filters.tags` 与 `filters.category` 命中不到都返回 0 而不是报错。门户检索试用页因此用 NSelect/NTreeSelect 绑这两个接口，回归测试见 `frontend-portal/src/tests/playground.spec.ts`。
- 门说"人工确认"就只能人确认：模型（包括复核用的代理）给同类产出打分不构成证据。`tag-accuracy-overrides.csv` 的 `verdict_by` 留空才算人手判定，`agent:` 前缀只算复核，`标签准确率` 这条门同时要求 `accuracy>=0.85`、样本 `>=100` 与**人手判定 `>=100`**（`acceptance_quality.MIN_HUMAN_VERDICTS`）。同理，任何"人工"字段都不要为了过门用脚本批量填值——那会把门的语义清空。
- 往 `RUNTIME_FIELDS` 加字段等于换了一版配置结构：`build_settings` 和 `restore_snapshot` 要求字段集完全一致，必须同步写数据迁移回填 `runtime_configurations.values`、`index_rebuilds.target_values`、`processing_tasks.config_snapshot` 三处，否则应用启动即 5002 失败。
- 渠道支付凭据只能经 `backend/app/services/payments/crypto.py` 进出库（HKDF 派生 + AES-256-GCM，AAD 绑 `channel_id|key_name|key_version`），明文永不进日志、审计、响应体；加密主密钥 `KNOFORGE_PAYMENT_MASTER_KEY` 只能放 `.env`，与被加密的凭据同库同源等于没加密。主密钥缺失时所有凭据读写必须 fail-closed 报 503/5003，不许降级成明文存储。
- `backend/app/services/payments/specs.py` 的 `CREDENTIAL_SPECS` 是渠道凭据字段的唯一事实源：加字段要同改 spec、provider 客户端与 `test_payment_specs.py`，管理端表单从 `GET /v1/admin/recharge/credential-specs` 渲染，前端不得复制一份字段清单。凭据 PATCH 是三态契约（键缺省=保留、有值=轮换且 `key_version+1`、null=清除），任何"总是把全部字段发出去"的写法都会把未改动密钥静默清空——`test_payment_credentials.py` 有守卫。
- 支付签名/验签与证书解析属 CPU 重活，请求与回调处理函数里必须 `asyncio.to_thread`（心跳回归见 `test_payment_selfcheck.py`）；厂商回调 handler 必须消费原始字节或原始表单字段，禁止经 Pydantic 反序列化后重编码再验签。live 用例唯一入口是 `KNOFORGE_LIVE_PAYMENT_TESTS=1` 加 `-m live`，默认全 skip，不许把真商户凭据写进仓库或夹具。
- 支付宝直连网关的待签名串必须是纯 ASCII：`common_params` 里 `biz_content` 用 `json.dumps(..., ensure_ascii=True)`。带未转义中文的 subject/店铺名会吃 `40002 isv.invalid-signature`（网关按 `charset` 解表单还原出的字节与我们签的 UTF-8 字节不一致），而错误提示会把人误导成"charset 没放进查询串"。参数放请求体还是查询串都能过，别把它改成"必须查询串"。回归测试：`test_common_params_escapes_non_ascii_in_biz_content`。
- 促销码只改 `payment_orders.payable_cent`（发给厂商、也是入账金额闸门的比对基准），**不改** `amount_cent + bonus_cent` 的到账金额：活动让掉的是收单的钱不是用户额度。`used_count` 只在订单入账时 +1，下单不占名额；被 `amount_mismatch` 挂起的单不再进对账扫描。折扣口径只在 `services/payments/promo.py::quote` 一处，门户结算页显示的数是它、下单时服务端重算的也是它，前端不得自己算折扣。
- 门户在线下单的交互形状是"磁贴选档 → `/console/recharge/checkout` 结算页 → 点扫码支付才下单"，档位不再用表格罗列。支付宝走 `alipay.trade.precreate` 拿 `qr_code` 出二维码，不做 page.pay 跳转与扫码两套并存。
- 在线入账只有一个写法：`backend/app/services/payments/settlement.py::mark_paid_and_credit`。它先 `SELECT ... FOR UPDATE` 锁订单行、读回当前状态，只有未 `paid` 的那次才追加流水——回调重投、门户轮询查单、beat 对账同时到达也只入一次账。任何"直接 `session.add(BalanceTransaction(...))` 给订单入账"的新路径都等于绕过幂等，`payment_order_id` 为空只允许人工入账使用。厂商回执金额与订单快照不一致时一分钱不记，写 `amount_mismatch` 事件挂起，管理端按 `?review=true` 筛；状态机允许 `expired/failed→paid`（迟到的支付必须还能入账），被挂起的单不再进 `reconcile` 扫描以免变成无限重试。
- 下单/查单/回调的厂商差异只准写在 `backend/app/services/payments/providers/` 里，路由通过 `registry.create_order|query_order|parse_notify` 分发（`parse_notify` 在 registry 内统一 `to_thread`，路由不要再各自包）。回给厂商的应答体各家不同（支付宝纯文本 `success`、微信 HTTP 200 + JSON、Stripe HTTP 200），集中在 `api/routes/payments.py::_ack/_reject`，回错文本厂商会无限重投。门户下单是"先落库再打厂商"：反过来的中途崩溃会留下厂商知道而我们不知道的单，所以订单行的 `commit` 必须发生在 `registry.create_order` 之前。

## 构建、测试与运行

- 初始化依赖：`.\scripts\setup.ps1`
- 全量检查：`.\scripts\check.ps1`
- 一键启动：`.\scripts\dev.ps1`
- 启动监控：`.\scripts\start-monitoring.ps1`（Prometheus + Grafana，凭据在 `data/monitoring/`）
- 停止容器：`.\scripts\stop.ps1`
- 备份与恢复：`.\scripts\backup.ps1`（pg_dump + 存储归档 + Qdrant 快照 + manifest）、`.\scripts\restore.ps1 -Path data\backup\<时间戳>`（需先停 dev.ps1，支持 `-WhatIf`）
- 验收脚本：`scripts/ingest_corpus.py`、`build_eval_dataset.py`、`acceptance_evaluation.py`、`acceptance_load.py`、`acceptance_concurrency.py`、`acceptance_quality.py`、`acceptance_sdk.py`、`acceptance_search.py`、`acceptance_monitoring.py`
- 运行配置：非敏感项存于 `runtime_configurations`，通过“系统设置”接口变更；模型密钥仍只读 `.env`
- 部署、切换与故障恢复步骤见 `docs/运行与恢复手册.md`
- 后端单测：根目录执行 `uv run python -m pytest backend/tests`
- 后端静态检查：`uv run ruff check backend scripts`、`uv run mypy backend/app`
- 数据库迁移：`uv run alembic -c backend/alembic.ini upgrade head`
- 前端开发：在 `frontend/` 执行 `npm run dev`
- 前端构建：在 `frontend/` 执行 `npm run build`（vue-tsc 类型检查 + Vite）
- 前端静态检查：在 `frontend/` 执行 `npm run lint`（ESLint + typescript-eslint + eslint-plugin-vue）
- 前端单测：在 `frontend/` 执行 `npm run test`（Vitest + Vue Test Utils + jsdom）
- 门户开发：在 `frontend-portal/` 执行 `npm run dev`（端口 5174；`.\scripts\dev.ps1` 会连同管理端一起起）
- 门户构建：在 `frontend-portal/` 执行 `npm run build`（vue-tsc 类型检查 + Vite）
- 门户静态检查：在 `frontend-portal/` 执行 `npm run lint`
- 门户单测：在 `frontend-portal/` 执行 `npm run test`
- Python SDK：`sdk/python/` 使用 HTTPX，与后端共用 Ruff、mypy 和 pytest；`uv build sdk/python` 通过 Hatchling 打包，不另建后端环境。
- JavaScript SDK：`sdk/javascript/` 使用 Node.js 内置 fetch 和 test runner；`npm --prefix sdk/javascript test` 验证，`npm pack ./sdk/javascript` 打包；仅服务端保存 API Key。

后端统一由根目录 `pyproject.toml` 与 `uv.lock` 管理，使用 `G:\Code_Warehouse\KnowForge\.venv\Scripts\python.exe`（Python 3.11+），不使用系统 Python 或另建后端环境。运行 uv 前必须在当前进程设置 `UV_PROJECT_ENVIRONMENT` 为项目根目录 `.venv`，覆盖机器级同名变量；PowerShell 脚本已显式设置，Git Bash 命令前加 `UV_PROJECT_ENVIRONMENT="G:/Code_Warehouse/KnowForge/.venv"`。Python 用 4 空格缩进并由 Ruff 检查；Vue/TypeScript 用 2 空格缩进并由 ESLint 检查。

模型调用统一使用 `openai.AsyncOpenAI`，通过 `KNOFORGE_MODEL_API_*` 配置 OpenAI 兼容地址与凭据；不要为单个模型另建 HTTP 客户端。

## 测试要求

新增功能必须配套测试，优先覆盖真实输入和回归场景。后端测试使用 `test_*.py`，前端组件测试使用 `*.spec.ts`。

如果改动影响跨文件协作、配置读取或对外输出，至少补一条能直接复现问题的测试。

测试 `process_task` 时必须在 fixture 会话之外建数据（它自己另开数据库连接，fixture 事务里的数据它看不到），并在结束时显式清理：规则与 LLM 打标会往 `tags` 插行，不清理会把后续测试撞死在 `uq_tags_name` 上。可参照 `backend/tests/test_pipeline_failures.py`：先记录已存在的 tag id，收尾时只删本次新增的。

## 修 Bug 纪律

- 先复现，再修复。
- 找根因，不修表象。
- 同类问题反复出现时，优先检查设计和边界条件。
- 修完后补回归测试，并验证受影响路径。

## Git 与 PR 规范

提交信息保持简短、聚焦单一变更，推荐使用 `feat:`、`fix:`、`refactor:` 这类前缀。

PR 里至少写清：
- 改了什么
- 怎么验证的
- 是否影响界面、配置或数据

如果有截图、日志或关键输出，放在 PR 描述里，方便复核。

## 安全与配置

把环境变量、缓存、凭据和本地生成物都当作敏感内容处理。需要配置时，优先提供 `.env.example` 或等价样例，而不是直接提交真实值。
