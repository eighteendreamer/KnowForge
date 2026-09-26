<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NInput, NScrollbar, NTabs, NTabPane, NTag, useMessage } from 'naive-ui'
import { ApiError, publicApi } from '../api/client'
import type { DocsData, DocsEndpoint } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import MarkdownBlock from '../components/MarkdownBlock.vue'

const docs = ref<DocsData | null>(null)
const failure = ref('')
const filter = ref('')
const active = ref('overview')
const message = useMessage()

const visible = computed(() =>
  (docs.value?.endpoints ?? []).filter((item) => {
    const needle = filter.value.trim().toLowerCase()
    return (
      !needle ||
      `${item.method} ${item.path} ${item.summary} ${item.purpose}`.toLowerCase().includes(needle)
    )
  })
)

async function load() {
  failure.value = ''
  try {
    docs.value = await publicApi<DocsData>('/api-docs')
  } catch (error) {
    failure.value = error instanceof ApiError ? error.message : String(error)
  }
}

// pre 会原样渲染模板里的换行缩进，所以整段示例在脚本里拼好再插值。
function sdkSample(sdk: DocsData['sdks'][number]) {
  return `${sdk.client}\n${sdk.methods.join('\n')}`
}

function anchorOf(item: DocsEndpoint) {
  return item.path.replace('/v1/knowledge/', '').replace(/[{}]/g, '').replace(/\//g, '-') || 'root'
}

function copy(text: string) {
  return navigator.clipboard
    .writeText(text)
    .then(() => message.success('已复制'))
    .catch(() => message.error('浏览器拒绝了剪贴板访问'))
}

onMounted(load)
</script>

<template>
  <PageHeader title="接口文档" description="内容实时来自 GET /v1/knowledge/api-docs，与管理端共用同一份定义。">
    <NButton size="small" :loading="!docs && !failure" @click="load">重新加载</NButton>
  </PageHeader>
  <NAlert v-if="failure" type="error">{{ failure }}</NAlert>
  <NAlert v-else-if="!docs" type="info">正在读取接口文档…</NAlert>
  <div v-if="docs" class="docs-layout">
    <NScrollbar class="docs-nav">
      <NInput v-model:value="filter" size="small" clearable placeholder="搜索接口" />
      <p class="docs-nav-label">概览</p>
      <a href="#doc-overview" :class="{ active: active === 'overview' }" @click="active = 'overview'">
        <span>接入信息</span>
      </a>
      <a href="#doc-quickstart" :class="{ active: active === 'quickstart' }" @click="active = 'quickstart'">
        <span>快速上手</span>
      </a>
      <a href="#doc-rate-limits" :class="{ active: active === 'rate' }" @click="active = 'rate'">
        <span>限流与配额</span>
      </a>
      <a href="#doc-performance" :class="{ active: active === 'perf' }" @click="active = 'perf'">
        <span>性能与容量</span>
      </a>
      <a href="#doc-sdks" :class="{ active: active === 'sdks' }" @click="active = 'sdks'">
        <span>官方 SDK</span>
      </a>
      <a href="#doc-errors" :class="{ active: active === 'errors' }" @click="active = 'errors'">
        <span>错误码</span>
      </a>
      <a href="#doc-changelog" :class="{ active: active === 'changelog' }" @click="active = 'changelog'">
        <span>更新记录</span>
      </a>
      <p class="docs-nav-label">接口（{{ visible.length }}）</p>
      <a
        v-for="item in visible"
        :key="item.path"
        :href="`#doc-${anchorOf(item)}`"
        :class="{ active: active === anchorOf(item) }"
        @click="active = anchorOf(item)"
      >
        <span>{{ item.summary || item.path }}</span>
        <span class="method">{{ item.method }}</span>
      </a>
    </NScrollbar>
    <div>
      <section id="doc-overview" class="docs-block">
        <h2>接入信息</h2>
        <div class="table-scroll">
          <table class="doc-table">
            <tbody>
              <tr><th>根地址</th><td><code>{{ docs.base_url }}</code></td></tr>
              <tr><th>鉴权</th><td>{{ docs.auth }}</td></tr>
              <tr><th>接口版本</th><td>{{ docs.version }}</td></tr>
              <tr>
                <th>成功信封</th>
                <td><MarkdownBlock :source="` \`${docs.envelope.success}\` `" inline /></td>
              </tr>
              <tr>
                <th>失败信封</th>
                <td><MarkdownBlock :source="` \`${docs.envelope.error}\` `" inline /></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section id="doc-quickstart" class="docs-block">
        <h2>快速上手</h2>
        <ol class="list-plain">
          <li v-for="row in docs.quickstart" :key="row.step">
            <h3>{{ row.step }}. {{ row.title }}</h3>
            <p><MarkdownBlock :source="row.detail" inline /></p>
          </li>
        </ol>
      </section>

      <section id="doc-rate-limits" class="docs-block">
        <h2>限流与配额</h2>
        <p class="muted">{{ docs.rate_limits.scope }} {{ docs.rate_limits.quota_note }}</p>
        <div class="table-scroll">
          <table class="doc-table" style="margin-top: 14px">
            <thead>
              <tr><th>计数器</th><th>默认</th><th>单位</th><th>口径</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in docs.rate_limits.counters" :key="row.name">
                <td class="name">{{ row.name }}</td>
                <td>{{ row.default }}</td>
                <td>{{ row.unit }}</td>
                <td>{{ row.meaning }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted" style="margin-top: 12px">
          超限：HTTP {{ docs.rate_limits.on_exceed.http }} / code {{ docs.rate_limits.on_exceed.code }} ·
          {{ docs.rate_limits.on_exceed.header }}。{{ docs.rate_limits.on_exceed.client_rule }}
        </p>
        <p class="muted" style="margin-top: 6px">{{ docs.rate_limits.shared_budget }}</p>
      </section>

      <section id="doc-performance" class="docs-block">
        <h2>性能与容量</h2>
        <p class="muted">{{ docs.performance.measure }}</p>
        <div class="table-scroll">
          <table class="doc-table" style="margin-top: 14px">
            <thead>
              <tr><th>场景</th><th>可持续</th><th>p95</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in docs.performance.throughput" :key="row.scenario">
                <td>{{ row.scenario }}</td>
                <td>{{ row.sustainable }}</td>
                <td>{{ row.p95 }}</td>
                <td>{{ row.note }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="table-scroll">
          <table class="doc-table" style="margin-top: 14px">
            <thead>
              <tr><th>单条查询</th><th>端到端</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in docs.performance.latency" :key="row.scenario">
                <td>{{ row.scenario }}</td>
                <td>{{ row.value }}</td>
                <td>{{ row.note }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted" style="margin-top: 12px">{{ docs.performance.bottleneck }}</p>
      </section>

      <section id="doc-sdks" class="docs-block">
        <h2>官方 SDK</h2>
        <div v-for="sdk in docs.sdks" :key="sdk.language" class="section">
          <h3>
            {{ sdk.language }} · <NTag size="small" :bordered="false">{{ sdk.package }}</NTag>
          </h3>
          <p class="muted">{{ sdk.requires }}。安装：<code>{{ sdk.install }}</code></p>
          <p class="muted">{{ sdk.note }}</p>
          <pre class="code-block">{{ sdkSample(sdk) }}</pre>
        </div>
      </section>

      <section id="doc-errors" class="docs-block">
        <h2>错误码</h2>
        <div class="table-scroll">
          <table class="doc-table">
            <thead>
              <tr><th>code</th><th>HTTP</th><th>含义与处置</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in docs.error_codes" :key="row.code">
                <td class="name">{{ row.code }}</td>
                <td>{{ row.http }}</td>
                <td>{{ row.meaning }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section id="doc-changelog" class="docs-block">
        <h2>更新记录</h2>
        <div v-for="row in docs.changelog" :key="row.date" class="section">
          <h3>{{ row.date }}</h3>
          <ul class="list-plain">
            <li v-for="change in row.changes" :key="change"><p><MarkdownBlock :source="change" inline /></p></li>
          </ul>
        </div>
      </section>

      <section
        v-for="item in visible"
        :id="`doc-${anchorOf(item)}`"
        :key="`${item.method}${item.path}`"
        class="docs-block docs-anchor"
      >
        <h2>
          <NTag size="small" :bordered="false" type="success">{{ item.method }}</NTag>
          <code>{{ item.path }}</code>
        </h2>
        <p class="muted">{{ item.summary }}</p>
        <p style="margin-top: 10px"><MarkdownBlock :source="item.purpose" inline /></p>
        <p class="muted" style="margin-top: 10px; font-size: 13px">
          {{ item.auth_required ? '需要 API Key（Authorization: Bearer）' : '无需 API Key' }}
        </p>

        <template v-if="item.request_fields.length">
          <h3>请求字段</h3>
          <div class="table-scroll">
            <table class="doc-table">
              <thead>
                <tr><th>字段</th><th>类型</th><th>必填</th><th>默认</th><th>说明</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in item.request_fields" :key="row.name">
                  <td class="name">{{ row.name }}</td>
                  <td class="type">{{ row.type }}</td>
                  <td><span v-if="row.required" class="badge-required">必填</span><span v-else>可选</span></td>
                  <td class="type">{{ row.default === null || row.default === undefined ? '—' : String(row.default) }}</td>
                  <td>{{ row.description || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>

        <h3>响应字段</h3>
        <div class="table-scroll">
          <table class="doc-table">
            <thead>
              <tr><th>字段</th><th>类型</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in item.response_fields" :key="row.field">
                <td class="name">{{ row.field }}</td>
                <td class="type">{{ row.type }}</td>
                <td>{{ row.meaning }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <template v-if="item.result_fields.length">
          <h3>results[] 元素字段</h3>
          <div class="table-scroll">
            <table class="doc-table">
              <thead>
                <tr><th>字段</th><th>类型</th><th>说明</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in item.result_fields" :key="row.field">
                  <td class="name">{{ row.field }}</td>
                  <td class="type">{{ row.type }}</td>
                  <td>{{ row.meaning }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>

        <template v-if="item.response_examples.success">
          <div class="doc-example-head"><h3>成功响应示例</h3><NButton size="tiny" tertiary @click="copy(item.response_examples.success!)">复制</NButton></div>
          <pre class="code-block">{{ item.response_examples.success }}</pre>
        </template>
        <template v-if="item.response_examples.failure">
          <div class="doc-example-head"><h3>失败响应示例</h3><NButton size="tiny" tertiary @click="copy(item.response_examples.failure!)">复制</NButton></div>
          <pre class="code-block">{{ item.response_examples.failure }}</pre>
        </template>

        <template v-if="item.notes.length">
          <h3>使用要点</h3>
          <ul class="list-plain">
            <li v-for="(note, index) in item.notes" :key="index">
              <p><MarkdownBlock :source="note" inline /></p>
            </li>
          </ul>
        </template>

        <h3>调用示例</h3>
        <NTabs type="line" animated size="small">
          <NTabPane v-for="example in item.examples" :key="example.language" :name="example.language" :tab="example.language">
            <div class="doc-example-head">
              <span class="muted">{{ example.language }}</span>
              <NButton size="tiny" tertiary @click="copy(example.code)">复制</NButton>
            </div>
            <pre class="code-block">{{ example.code }}</pre>
          </NTabPane>
        </NTabs>
      </section>
    </div>
  </div>
</template>
