<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NInput, NTabs, NTabPane, NTag } from 'naive-ui'
import { publicApi } from '../api/client'
import { useAction } from '../api/feedback'
import MarkdownBlock from '../components/MarkdownBlock.vue'
import PageHeader from '../components/PageHeader.vue'
import { useMessage } from 'naive-ui'

interface FieldRow { name: string; type: string; required: boolean; default: unknown; description: string }
interface Endpoint {
  method: string
  path: string
  summary: string
  purpose: string
  notes: string[]
  auth_required: boolean
  request_fields: FieldRow[]
  response_fields: { field: string; type: string; meaning: string }[]
  result_fields: { field: string; type: string; meaning: string }[]
  response_examples: { success?: string; failure?: string }
  examples: { language: string; code: string }[]
  responses: string[]
}
interface Docs { base_url: string; auth: string; envelope: { success: string; error: string }; error_codes: { code: number; http: number; meaning: string }[]; endpoints: Endpoint[] }

const { busy, run } = useAction()
const message = useMessage()
const docs = ref<Docs | null>(null)
const filter = ref('')
const active = ref('')
const visible = computed(() => (docs.value?.endpoints ?? []).filter((item) =>
  !filter.value.trim() || `${item.method} ${item.path} ${item.summary} ${item.purpose}`.toLowerCase().includes(filter.value.trim().toLowerCase())))
const selected = computed(() => visible.value.find((item) => `${item.method} ${item.path}` === active.value) ?? visible.value[0] ?? null)

async function load() {
  docs.value = await publicApi<Docs>('/api-docs')
}
function copy(text: string) {
  return navigator.clipboard.writeText(text).then(() => message.success('示例已复制'), () => message.error('浏览器拒绝了剪贴板访问'))
}
onMounted(() => void run(load))
</script>

<template>
  <PageHeader title="接口文档" description="仅列出对外开放的 /v1/knowledge 接口；管理端接口不会出现在这里。">
    <NInput v-model:value="filter" clearable placeholder="按路径或能力搜索接口" class="docs-filter" />
  </PageHeader>
  <NAlert v-if="busy && !docs" type="info">正在读取接口清单…</NAlert>
  <p v-if="docs" class="muted docs-base">接入根地址 <code>{{ docs.base_url }}</code>，{{ docs.auth }}</p>
  <div v-if="docs" class="docs-layout">
    <nav class="docs-list">
      <button
        v-for="item in visible"
        :key="`${item.method} ${item.path}`"
        class="docs-item"
        :class="{ active: `${item.method} ${item.path}` === `${selected?.method} ${selected?.path}` }"
        @click="active = `${item.method} ${item.path}`"
      >
        <NTag size="small" :bordered="false" :type="item.method === 'GET' ? 'info' : 'success'">{{ item.method }}</NTag>
        <span class="docs-item-path">{{ item.path.replace('/v1/knowledge', '') || '/' }}</span>
        <span class="docs-item-summary">{{ item.summary }}</span>
      </button>
      <p v-if="!visible.length" class="muted">没有匹配的接口。</p>
    </nav>
    <section v-if="selected" class="docs-detail">
      <h2>{{ selected.method }} {{ selected.path }}</h2>
      <p class="docs-purpose"><MarkdownBlock :source="selected.purpose" inline /></p>
      <p class="muted">
        {{ selected.auth_required ? '需要 API Key（' + docs.auth + '）' : '无需鉴权' }} · 响应状态 {{ selected.responses.join(' / ') || '200' }}
      </p>
      <ul class="docs-notes">
        <li v-for="note in selected.notes" :key="note"><MarkdownBlock :source="note" inline /></li>
      </ul>
      <h3 v-if="selected.request_fields.length">请求参数</h3>
      <table v-if="selected.request_fields.length" class="docs-table">
        <thead><tr><th>字段</th><th>类型</th><th>必填</th><th>默认</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="field in selected.request_fields" :key="field.name">
            <td><code>{{ field.name }}</code></td>
            <td>{{ field.type }}</td>
            <td>{{ field.required ? '是' : '否' }}</td>
            <td>{{ field.default === null || field.default === undefined ? '—' : JSON.stringify(field.default) }}</td>
            <td>{{ field.description || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <h3 v-if="selected.response_fields.length">返回字段</h3>
      <table v-if="selected.response_fields.length" class="docs-table">
        <thead><tr><th>字段</th><th>类型</th><th>含义</th></tr></thead>
        <tbody>
          <tr v-for="field in selected.response_fields" :key="field.field">
            <td><code>{{ field.field }}</code></td><td>{{ field.type }}</td><td>{{ field.meaning }}</td>
          </tr>
        </tbody>
      </table>
      <h3 v-if="selected.result_fields.length">results[] 元素字段</h3>
      <table v-if="selected.result_fields.length" class="docs-table">
        <thead><tr><th>字段</th><th>类型</th><th>含义</th></tr></thead>
        <tbody>
          <tr v-for="field in selected.result_fields" :key="field.field">
            <td><code>{{ field.field }}</code></td><td>{{ field.type }}</td><td>{{ field.meaning }}</td>
          </tr>
        </tbody>
      </table>
      <template v-if="selected.response_examples.success">
        <h3>成功响应示例</h3>
        <div class="code-block"><pre>{{ selected.response_examples.success }}</pre></div>
      </template>
      <template v-if="selected.response_examples.failure">
        <h3>失败响应示例</h3>
        <div class="code-block"><pre>{{ selected.response_examples.failure }}</pre></div>
      </template>
      <h3>调用示例</h3>
      <NTabs type="line" animated>
        <NTabPane v-for="example in selected.examples" :key="example.language" :name="example.language" :tab="example.language">
          <div class="code-block">
            <NButton size="small" quaternary class="code-copy" @click="copy(example.code)">复制</NButton>
            <pre>{{ example.code }}</pre>
          </div>
        </NTabPane>
      </NTabs>
    </section>
  </div>
  <section v-if="docs" class="docs-errors">
    <h3>统一响应与错误码</h3>
    <p class="muted">成功：<code>{{ docs.envelope.success }}</code>；失败：<code>{{ docs.envelope.error }}</code></p>
    <table class="docs-table">
      <thead><tr><th>code</th><th>HTTP</th><th>含义与处理</th></tr></thead>
      <tbody><tr v-for="item in docs.error_codes" :key="item.code"><td>{{ item.code }}</td><td>{{ item.http }}</td><td>{{ item.meaning }}</td></tr></tbody>
    </table>
  </section>
</template>

<style scoped>
.docs-base { margin: 0 0 16px; font-size: 13px; }
.docs-filter { width: 280px; }
.docs-layout { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 28px; align-items: start; }
.docs-list { display: grid; gap: 2px; border-right: 1px solid #eceeed; padding-right: 12px; }
.docs-item { display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; text-align: left; background: none; border: 0; border-left: 2px solid transparent; padding: 8px 10px; cursor: pointer; color: inherit; font: inherit; }
.docs-item:hover { background: #f5f7f6; }
.docs-item[aria-current='true'], .docs-item.active { border-left-color: #18a058; background: #f2f8f4; }
.docs-item-path { font-family: ui-monospace, Menlo, monospace; font-size: 13px; }
.docs-item-summary { grid-column: 1 / -1; color: #6b7772; font-size: 12px; }
.docs-detail h2 { font-size: 17px; margin: 0 0 8px; }
.docs-detail h3 { font-size: 14px; margin: 24px 0 8px; }
.docs-purpose { max-width: 78ch; }
.docs-notes { margin: 12px 0 0; padding-left: 20px; display: grid; gap: 4px; font-size: 13px; color: #46514c; }
.docs-table { border-collapse: collapse; width: 100%; font-size: 13px; }
.docs-table th, .docs-table td { border-bottom: 1px solid #eceeed; padding: 7px 8px; text-align: left; vertical-align: top; }
.docs-table th { color: #6b7772; font-weight: 500; }
.code-block { position: relative; background: #f7f8f7; border-radius: 4px; padding: 10px 12px; }
.code-block pre { margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 12.5px; }
.code-copy { position: absolute; top: 6px; right: 8px; }
.docs-errors { margin-top: 36px; }
.docs-errors h3 { font-size: 14px; margin: 0 0 10px; }
@media (max-width: 1100px) { .docs-layout { grid-template-columns: 1fr; } .docs-list { border-right: 0; } }
</style>
