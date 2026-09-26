<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NAlert, NButton, NIcon, NTabs, NTabPane, useMessage } from 'naive-ui'
import {
  AlertCircleOutline,
  AnalyticsOutline,
  BookOutline,
  ChatboxEllipsesOutline,
  GitBranchOutline,
  KeyOutline,
  TextOutline,
} from '@vicons/ionicons5'
import type { Component } from 'vue'
import { errorMessage, publicApi } from '../api/client'
import type { DocsData } from '../api/types'
import { useAuthStore } from '../stores/auth'
import GateHeader from '../components/GateHeader.vue'
import MarkdownBlock from '../components/MarkdownBlock.vue'

const message = useMessage()
const auth = useAuthStore()
const docs = ref<DocsData | null>(null)
const failure = ref('')

// 只有落地页隐藏滚动条；控制台是应用界面，滚动位置是有效信息，要留着。
onMounted(() => document.documentElement.classList.add('hide-scrollbar'))
onUnmounted(() => document.documentElement.classList.remove('hide-scrollbar'))

const LANGUAGE_LABELS: Record<string, string> = {
  curl: 'curl',
  python: 'Python',
  javascript: 'JavaScript',
}

function pretty(json: string) {
  try {
    return JSON.stringify(JSON.parse(json), null, 2)
  } catch {
    return json
  }
}

// 代码面板与结果字段表全部取自 GET /v1/knowledge/api-docs 的实时输出，页面自己不抄一份契约。
const searchEndpoint = computed(() => docs.value?.endpoints.find((row) => row.path.endsWith('/search')))

const snippets = computed(() => {
  const endpoint = searchEndpoint.value
  if (!endpoint) return []
  const tabs = endpoint.examples.map((item) => ({
    key: item.language,
    label: LANGUAGE_LABELS[item.language] ?? item.language,
    code: item.code,
  }))
  const success = endpoint.response_examples.success
  if (success) tabs.push({ key: 'response', label: '响应示例', code: pretty(success) })
  return tabs
})

const activeSnippet = ref('curl')
const activeCode = computed(
  () => snippets.value.find((item) => item.key === activeSnippet.value)?.code ?? snippets.value[0]?.code ?? ''
)

function copyCode() {
  if (!activeCode.value) return
  return navigator.clipboard
    .writeText(activeCode.value)
    .then(() => message.success('已复制'))
    .catch(() => message.error('浏览器拒绝了剪贴板访问'))
}

const resultFields = computed(() => searchEndpoint.value?.result_fields ?? [])

const modes: { name: string; icon: Component; recall: string; model: string; auto: string; use: string }[] = [
  {
    name: 'hybrid',
    icon: GitBranchOutline,
    recall: '语义 + 关键词 + 字面近似三路召回，RRF 融合',
    model: '默认叠加一次远程精排',
    auto: '会（叙述、提问型查询）',
    use: '默认推荐；不在乎多一次精排调用时用它',
  },
  {
    name: 'keyword',
    icon: TextOutline,
    recall: 'BM25 稀疏向量，按术语、类名、配置项、错误码命中',
    model: '不碰模型（关掉精排就是纯索引路径）',
    auto: '会（以英文标识符为主体的查询）',
    use: '查询词就是几个英文术语，或要压延迟时显式选它',
  },
  {
    name: 'semantic',
    icon: AnalyticsOutline,
    recall: 'Dense 向量语义匹配',
    model: '每次要算一个查询向量',
    auto: '不会（没有可依据的词面信号）',
    use: '说法和文档用词不一致、只要语义召回时',
  },
  {
    name: 'fuzzy',
    icon: AlertCircleOutline,
    recall: 'PostgreSQL pg_trgm 字面近似，抗拼写误差',
    model: '不碰模型',
    auto: '不会（无法判断“拼写不确定”）',
    use: '术语可能打错字时',
  },
]

const sections = [
  { id: 'modes', label: '四种检索模式' },
  { id: 'fields', label: '返回字段与可引用性' },
  { id: 'capacity', label: '实测容量' },
  { id: 'start', label: '接入方式' },
  { id: 'errors', label: '错误码与限流' },
]

// 这三条是使用这套接口最容易踩空的地方，页面必须替调用方写清楚，不能等人拼错一次才发现。
const boundaries = [
  {
    title: '过滤项只能取服务端的值',
    body: 'filters.tags 用 GET /v1/knowledge/tags 的返回（只含已过审标签，带篇数，可用 ?category= 按子树收窄），'
      + 'filters.category 用 GET /v1/knowledge/categories 的完整路径。自己拼一个不存在或未过审的标签不会报错，'
      + '只会把结果过滤成空。',
  },
  {
    title: '空结果要分清两种',
    body: '不带过滤时 results 为空表示知识库没有依据，应答“知识库未覆盖”，不补写内容；'
      + '带 filters 时为空只说明这组条件圈不到东西，去掉过滤复核一次再下结论。',
  },
  {
    title: '分数不是置信度',
    body: 'score_type 说明分数来自 rrf / semantic / keyword / fuzzy / rerank，不同模式之间不可比较；'
      + 'score_calibrated 为 false 时不要自建阈值，也不要拿它当准确率。',
  },
]

function sdkSample(sdk: DocsData['sdks'][number]) {
  // pre 会原样渲染模板里的换行缩进，所以整段示例在脚本里拼好再插值。
  return `${sdk.client}\n${sdk.methods.join('\n')}`
}

const facts: { icon: Component; title: string; body: string }[] = [
  {
    icon: BookOutline,
    title: '命中即引用',
    body: '每条结果带原始文件名、页码与章节路径，再用 lookup 与原文下载接口对回一手材料。',
  },
  {
    icon: ChatboxEllipsesOutline,
    title: '一个接口四种模式',
    body: 'search_type 显式指定，或交给 auto 按查询意图在 keyword 与 hybrid 之间选路。',
  },
  {
    icon: KeyOutline,
    title: '没覆盖就说没覆盖',
    body: '不带过滤时结果为空就是知识库没有依据；接口不会补写内容，也不给假的相似度分数。',
  },
]

publicApi<DocsData>('/api-docs')
  .then((data) => {
    docs.value = data
  })
  .catch((error) => {
    failure.value = errorMessage(error)
  })
</script>

<template>
  <GateHeader />
  <main>
    <section id="intro" class="intro">
      <div class="intro-grid">
        <div class="intro-copy">
          <p class="eyebrow">KnowForge 开放接口</p>
          <h1>把技术文档变成<span class="accent">可引用的答案</span>，而不是可猜的文本</h1>
          <p class="lead">
            上传 PDF 与 HTML，系统完成解析、分块、向量化与自动打标；对外只暴露一个检索接口，
            返回带来源文件名、页码和章节路径的分块原文。命中不到就返回空列表，不会补写内容。
          </p>
          <div class="row cta-row">
            <RouterLink v-if="auth.token" to="/console/overview">
              <NButton type="primary" size="large">进入控制台</NButton>
            </RouterLink>
            <template v-else>
              <RouterLink to="/register"><NButton type="primary" size="large">免费注册</NButton></RouterLink>
              <RouterLink to="/login"><NButton size="large" quaternary>已有账号，登录</NButton></RouterLink>
            </template>
            <RouterLink to="/console/playground"><NButton size="large" tertiary>拿 Key 试一条查询</NButton></RouterLink>
          </div>
        </div>
        <div class="intro-demo">
          <div class="demo-head">
            <code v-if="searchEndpoint">{{ searchEndpoint.method }} {{ searchEndpoint.path }}</code>
            <span v-else class="muted">示例来自 GET /v1/knowledge/api-docs</span>
            <NButton v-if="activeCode" size="tiny" tertiary @click="copyCode">复制</NButton>
          </div>
          <NTabs v-if="snippets.length" v-model:value="activeSnippet" type="segment" size="small" animated>
            <NTabPane v-for="item in snippets" :key="item.key" :name="item.key" :tab="item.label">
              <pre class="code-block demo-code">{{ item.code }}</pre>
            </NTabPane>
          </NTabs>
          <p v-else class="muted demo-empty">
            {{ failure ? `接口清单未加载：${failure}` : '正在载入接口清单…' }}
          </p>
          <p class="demo-caption">
            代码示例、响应示例与下面的字段表都取自 <code>GET /v1/knowledge/api-docs</code> 的实时输出，
            页面不另抄一份契约。
          </p>
        </div>
      </div>

      <ul class="facts">
        <li v-for="fact in facts" :key="fact.title">
          <h3>
            <NIcon :size="16" color="#18a058"><component :is="fact.icon" /></NIcon>
            {{ fact.title }}
          </h3>
          <p>{{ fact.body }}</p>
        </li>
      </ul>

      <div v-if="docs" class="intro-stats">
        <div class="intro-stat">
          <strong>{{ docs.endpoints.length }}</strong>
          <span>对外开放接口</span>
        </div>
        <div class="intro-stat">
          <strong>{{ docs.error_codes.length }}</strong>
          <span>会返回的错误码</span>
        </div>
        <div class="intro-stat">
          <strong>{{ docs.rate_limits.counters.map((row) => row.default).join(' / ') }}</strong>
          <span>每分钟 / 每日配额</span>
        </div>
        <div class="intro-stat">
          <strong>{{ docs.version }}</strong>
          <span>接口版本</span>
        </div>
      </div>
    </section>

    <NAlert v-if="failure" type="warning" class="band">
      容量与接口数据暂未加载，下面只展示不依赖接口的部分：{{ failure }}
    </NAlert>

    <div v-if="docs" class="doc-shell">
      <aside class="doc-toc">
        <p class="toc-label">系统介绍</p>
        <nav>
          <a v-for="section in sections" :key="section.id" :href="`#${section.id}`">{{ section.label }}</a>
        </nav>
        <p class="toc-label">接着做</p>
        <nav>
          <RouterLink to="/console/playground">检索试用</RouterLink>
          <RouterLink to="/console/docs">完整接口文档</RouterLink>
          <RouterLink to="/console/keys">管理 API Key</RouterLink>
        </nav>
      </aside>

      <div class="doc-main">
        <section id="modes" class="doc-section">
          <div class="section-head">
            <h2>四种检索模式，一个接口</h2>
            <p>同一个 <code>POST /search</code> 用 <code>search_type</code> 切换，最终生效模式回读 <code>search_type_used</code>。</p>
          </div>
          <div class="table-scroll">
            <table class="table-plain">
              <thead>
                <tr>
                  <th>模式</th>
                  <th>靠什么召回</th>
                  <th>远程模型</th>
                  <th>auto 会不会选</th>
                  <th>什么时候用它</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in modes" :key="row.name">
                  <td class="mode-cell">
                    <NIcon :size="15" color="#18a058"><component :is="row.icon" /></NIcon>
                    {{ row.name }}
                  </td>
                  <td>{{ row.recall }}</td>
                  <td>{{ row.model }}</td>
                  <td>{{ row.auto }}</td>
                  <td class="muted">{{ row.use }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section id="fields" class="doc-section">
          <div class="section-head">
            <h2>返回字段与可引用性</h2>
            <p>
              字段含义由服务端自述清单给出，不在这里复述：<code>lookup</code> 能按文档取回原文与分块，
              <code>documents/{doc_id}/file</code> 直接给原始 PDF 或 HTML 字节流，作为回答的一手凭据。
            </p>
          </div>
          <div class="table-scroll">
            <table class="table-plain">
              <thead>
                <tr><th>字段</th><th>类型</th><th>含义</th></tr>
              </thead>
              <tbody>
                <tr v-for="field in resultFields" :key="field.field">
                  <td class="field-name"><code>{{ field.field }}</code></td>
                  <td class="muted">{{ field.type }}</td>
                  <td>{{ field.meaning }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <dl class="boundaries">
            <div v-for="item in boundaries" :key="item.title">
              <dt>{{ item.title }}</dt>
              <dd>{{ item.body }}</dd>
            </div>
          </dl>
        </section>

        <section id="capacity" class="doc-section">
          <div class="section-head">
            <h2>实测容量，不是宣传数字</h2>
            <p>{{ docs.performance.measure }}</p>
          </div>
          <!-- 接口数/错误码/配额这几个数已经在首屏数据条里了，这里只讲实测吞吐，不重复一遍。 -->
          <div class="table-scroll">
            <table class="table-plain">
              <thead>
                <tr><th>场景</th><th>可持续吞吐</th><th>p95</th><th>说明</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in docs.performance.throughput" :key="row.scenario">
                  <td>{{ row.scenario }}</td>
                  <td><strong>{{ row.sustainable }}</strong></td>
                  <td>{{ row.p95 }}</td>
                  <td class="muted">{{ row.note }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <h3 class="sub-title">单条查询延迟</h3>
          <div class="table-scroll">
            <table class="table-plain">
              <thead>
                <tr><th>调用方式</th><th>端到端</th><th>说明</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in docs.performance.latency" :key="row.scenario">
                  <td>{{ row.scenario }}</td>
                  <td><strong>{{ row.value }}</strong></td>
                  <td class="muted">{{ row.note }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="muted bottleneck">{{ docs.performance.bottleneck }}</p>
        </section>

        <section id="start" class="doc-section">
          <div class="section-head">
            <h2>接入方式</h2>
            <p>注册后在控制台建一把只读密钥即可开始；密钥明文只显示一次，服务端只保存 SHA-256 摘要。</p>
          </div>
          <ol class="steps">
            <li v-for="row in docs.quickstart" :key="row.step">
              <span class="step-no">{{ row.step }}</span>
              <div>
                <h3>{{ row.title }}</h3>
                <p><MarkdownBlock :source="row.detail" inline /></p>
              </div>
            </li>
          </ol>
          <div class="sdk-row">
            <div v-for="sdk in docs.sdks" :key="sdk.language" class="sdk-block">
              <h3>
                {{ sdk.language }}
                <code class="pkg">{{ sdk.package }}</code>
              </h3>
              <p class="muted">{{ sdk.requires }} · 安装 <code>{{ sdk.install }}</code></p>
              <p class="muted">{{ sdk.note }}</p>
              <pre class="code-block">{{ sdkSample(sdk) }}</pre>
            </div>
          </div>
        </section>

        <section id="errors" class="doc-section">
          <div class="section-head">
            <h2>错误码与限流</h2>
            <p>{{ docs.rate_limits.scope }} {{ docs.rate_limits.quota_note }}</p>
          </div>
          <div class="table-scroll">
            <table class="table-plain">
              <thead>
                <tr><th>code</th><th>HTTP</th><th>含义</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in docs.error_codes" :key="row.code">
                  <td><code>{{ row.code }}</code></td>
                  <td>{{ row.http }}</td>
                  <td>{{ row.meaning }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="muted bottleneck">
            超限返回 {{ docs.rate_limits.on_exceed.http }} / code {{ docs.rate_limits.on_exceed.code }}，
            响应头 {{ docs.rate_limits.on_exceed.header }}。{{ docs.rate_limits.on_exceed.client_rule }}
            {{ docs.rate_limits.shared_budget }}
          </p>
        </section>
      </div>
    </div>

    <section class="cta">
      <div>
        <h2>开始使用</h2>
        <p class="muted">注册即开通控制台，先在检索试用里把一条查询跑通，再把密钥接到你自己的服务里。</p>
      </div>
      <div class="row">
        <RouterLink
          v-if="!auth.token"
          to="/register"
        >
          <NButton type="primary" size="large">注册并进入控制台</NButton>
        </RouterLink>
        <RouterLink
          v-if="auth.token"
          to="/console/playground"
        >
          <NButton type="primary" size="large">去检索试用</NButton>
        </RouterLink>
        <RouterLink to="/console/docs"><NButton size="large" quaternary>看接口文档</NButton></RouterLink>
      </div>
    </section>
  </main>
  <footer class="gate-footer">
    <span>KnowForge · 技术知识库检索服务</span>
    <span>{{ docs ? `接口清单版本 ${docs.version}` : '接口清单以 GET /v1/knowledge/api-docs 实时输出为准' }}</span>
  </footer>
</template>

<style scoped>
.intro {
  max-width: 1280px;
  margin: 0 auto;
  padding: 72px 40px 0;
  border-bottom: 1px solid #efeff5;
}
.intro-grid {
  display: grid;
  grid-template-columns: minmax(400px, 1.02fr) minmax(400px, 0.98fr);
  gap: 56px;
  align-items: start;
}
.eyebrow {
  font-size: 12px;
  letter-spacing: 1.6px;
  color: #18a058;
  text-transform: uppercase;
  margin-bottom: 16px;
}
.intro h1 {
  font-size: clamp(30px, 3.1vw, 44px);
  line-height: 1.24;
  max-width: 15em;
  text-wrap: balance;
}
.intro h1 .accent {
  color: #18a058;
  /* 中文可以逐字断行，不锁住就会断成"可引用的答/案"。 */
  display: inline-block;
  white-space: nowrap;
}
.lead {
  font-size: 16px;
  color: #5c6662;
  margin-top: 20px;
  max-width: 620px;
}
.cta-row {
  margin-top: 30px;
  flex-wrap: wrap;
}
.facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 56px 0 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid #efeff5;
}
.facts li {
  padding: 22px 32px 26px 0;
}
.facts li + li {
  padding-left: 32px;
  border-left: 1px solid #efeff5;
}
.facts h3 {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  margin-bottom: 6px;
}
.facts p {
  font-size: 13.5px;
  color: #767c82;
  line-height: 1.75;
}
.intro-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  margin: 0 -40px;
  padding: 0 40px;
  border-top: 1px solid #efeff5;
}
.intro-stat {
  padding: 22px 32px 26px;
  border-left: 1px solid #efeff5;
}
.intro-stat:first-child {
  border-left: 0;
  padding-left: 0;
}
.intro-stat strong {
  display: block;
  font-size: 22px;
  font-weight: 600;
  line-height: 1.5;
  letter-spacing: -0.3px;
}
.intro-stat span {
  font-size: 12.5px;
  color: #767c82;
}
.intro-demo {
  min-width: 0;
  padding-top: 34px;
}
.demo-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 0 10px;
  font-size: 13px;
  color: #5c6662;
}
.demo-code {
  max-height: 320px;
  font-size: 12.5px;
  white-space: pre-wrap;
  word-break: break-word;
}
.demo-caption {
  font-size: 12.5px;
  color: #767c82;
  line-height: 1.75;
  margin-top: 12px;
}
.demo-empty {
  font-size: 13px;
  padding: 28px 0;
  border: 1px dashed #e3e6e4;
  text-align: center;
}
.doc-shell {
  display: grid;
  grid-template-columns: 196px minmax(0, 1fr);
  gap: 52px;
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 40px;
}
.doc-toc {
  position: sticky;
  top: 92px;
  align-self: start;
  padding: 34px 0 20px;
}
.doc-toc nav {
  display: flex;
  flex-direction: column;
}
.doc-toc a {
  padding: 7px 0;
  font-size: 13.5px;
  color: #5c6662;
  border-left: 2px solid transparent;
  padding-left: 12px;
  margin-left: -12px;
}
.doc-toc a:hover {
  color: #18a058;
  border-left-color: #cfe6d8;
}
.toc-label {
  font-size: 11px;
  letter-spacing: 1.2px;
  color: #9aa0a6;
  text-transform: uppercase;
  margin: 22px 0 6px;
}
.toc-label:first-child {
  margin-top: 0;
}
.doc-main {
  min-width: 0;
}
.doc-section {
  padding: 60px 0;
  border-bottom: 1px solid #efeff5;
  scroll-margin-top: 92px;
}
.doc-section:first-child {
  padding-top: 34px;
}
.section-head {
  max-width: 760px;
  margin-bottom: 24px;
}
.section-head p {
  color: #5c6662;
  font-size: 14px;
  margin-top: 8px;
}
.mode-cell,
.field-name {
  white-space: nowrap;
}
.mode-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace;
  font-size: 13px;
}
.field-name code {
  color: #137a43;
}
.boundaries {
  margin: 30px 0 0;
}
.boundaries > div {
  display: grid;
  grid-template-columns: 168px minmax(0, 1fr);
  gap: 18px;
  padding: 14px 0;
  border-top: 1px solid #efeff5;
}
.boundaries dt {
  font-size: 13.5px;
  font-weight: 600;
}
.boundaries dd {
  margin: 0;
  font-size: 13.5px;
  color: #5c6662;
  line-height: 1.75;
}
.sub-title {
  font-size: 14px;
  color: #5c6662;
  margin: 32px 0 10px;
}
.bottleneck {
  font-size: 13px;
  margin-top: 16px;
  line-height: 1.75;
}
.steps {
  margin: 0;
  padding: 0;
  list-style: none;
  counter-reset: step;
}
.steps li {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: 14px;
  padding: 18px 0;
  border-top: 1px solid #efeff5;
}
.steps li:first-child {
  border-top: 0;
  padding-top: 0;
}
.step-no {
  font-size: 13px;
  color: #18a058;
  font-variant-numeric: tabular-nums;
  padding-top: 2px;
}
.steps h3 {
  margin-bottom: 4px;
}
.steps p {
  font-size: 14px;
  color: #5c6662;
}
.sdk-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 32px;
  margin-top: 34px;
}
.sdk-block h3 {
  display: flex;
  align-items: center;
  gap: 10px;
}
.pkg {
  font-size: 12px;
  color: #137a43;
  background: #f2f7f4;
  padding: 2px 6px;
}
.sdk-block p {
  font-size: 13px;
  margin-top: 4px;
}
.sdk-block .code-block {
  margin-top: 12px;
}
.cta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 32px;
  flex-wrap: wrap;
  max-width: 1280px;
  margin: 0 auto;
  padding: 64px 40px 76px;
}
.cta p {
  font-size: 14px;
  margin-top: 6px;
}
@media (max-width: 1080px) {
  .intro {
    padding: 48px 20px 0;
  }
  .intro-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 34px;
  }
  .intro-demo {
    padding-top: 0;
  }
  .facts {
    grid-template-columns: minmax(0, 1fr);
    margin-top: 40px;
  }
  .facts li,
  .facts li + li {
    padding: 16px 0;
    border-left: 0;
    border-top: 1px solid #efeff5;
  }
  .facts li:first-child {
    border-top: 0;
    padding-top: 0;
  }
  .intro-stats {
    margin: 0 -20px;
    padding: 0 20px;
    display: block;
  }
  .intro-stat,
  .intro-stat:first-child {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 14px;
    padding: 12px 0;
    border-left: 0;
    border-top: 1px solid #efeff5;
  }
  .intro-stat:first-child {
    border-top: 0;
  }
  .doc-shell {
    grid-template-columns: 1fr;
    gap: 0;
    padding: 0 20px;
  }
  .doc-toc {
    position: static;
    padding: 24px 0 0;
  }
  .doc-toc nav {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 4px 20px;
  }
  .doc-toc a {
    border-left: 0;
    padding-left: 0;
    margin-left: 0;
  }
  .doc-section {
    padding: 44px 0;
  }
}
</style>
