<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, reactive, ref } from 'vue'
import { NAlert, NButton, NDataTable, NDescriptions, NDescriptionsItem, NForm, NFormItem, NInput, NInputNumber, NModal, NSpace, NSwitch, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { api } from '../api/client'
import { formatDate, useAction } from '../api/feedback'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import { useDialog } from 'naive-ui'

export interface RebuildRow {
  id: string
  target_configuration_id: string
  target_collection: string
  status: string
  total_documents: number
  completed_documents: number
  failed_documents: number
  evaluation_id: number | null
  error_message: string | null
  created_at: string
  updated_at: string
}
export interface EvaluationRow {
  id: number
  name: string
  dataset_kind: string
  collection: string
  metrics: Record<string, number>
  passed: boolean
}
export interface SettingsView {
  configuration_id: string
  index_fingerprint: string
  values: Record<string, string | number | boolean | null>
  editable_fields: string[]
  rebuild_fields: string[]
  models_configured: boolean
  rebuilds: RebuildRow[]
  evaluations: EvaluationRow[]
}

const LABELS: Record<string, string> = {
  model_rerank_path: 'Rerank 路径',
  model_timeout_seconds: '模型超时（秒）',
  model_max_retries: '模型重试次数',
  model_max_concurrency: '模型并发上限',
  model_requests_per_minute: '模型每分钟请求数',
  rerank_model: 'Rerank 模型',
  llm_model: '标签/改写模型',
  vl_model: '扫描页视觉模型',
  rerank_enabled: '启用精排',
  upload_max_bytes: '上传大小上限（字节）',
  pdf_max_pages: 'PDF 页数上限',
  pdf_max_pixels: 'PDF 单页像素上限',
  embedding_batch_size: 'Embedding 批量',
  cache_ttl_seconds: '检索缓存 TTL（秒）',
  tag_auto_approve_confidence: '标签自动通过置信度',
}
const NUMERIC = new Set(Object.keys(LABELS).filter((key) => !LABELS[key].includes('路径') && !LABELS[key].includes('模型')))
const BOOLEAN = new Set(['rerank_enabled'])

const { busy, run } = useAction()
const dialog = useDialog()
const view = ref<SettingsView | null>(null)
const texts = reactive<Record<string, string>>({})
const numbers = reactive<Record<string, number | null>>({})
const flags = reactive<Record<string, boolean>>({})
const rebuildOpen = ref(false)
const rebuildForm = reactive({ embedding_model: '', embedding_dimension: 1024 })
const evaluateOpen = ref(false)
const evaluateForm = reactive({
  rebuildId: '', name: '', datasetVersion: '', datasetHash: '',
  recallAt5: 0.9, mrr: 0.8, ndcgAt10: 0.85, precisionAt5: 0.85,
})

const fields = computed(() => (view.value?.editable_fields ?? []).filter((field) => LABELS[field]))
const changes = computed<Record<string, string | number | boolean>>(() => {
  if (!view.value) return {}
  const draft: Record<string, string | number | boolean> = {}
  for (const field of fields.value) {
    if (BOOLEAN.has(field)) draft[field] = flags[field]
    else if (NUMERIC.has(field)) draft[field] = numbers[field] ?? 0
    else draft[field] = texts[field]
  }
  return Object.fromEntries(Object.entries(draft).filter(([key, value]) => view.value?.values[key] !== value))
})
const activeRebuild = computed(() => view.value?.rebuilds.find((row) => ['evaluating', 'ready'].includes(row.status)) ?? null)

function adopt(data: SettingsView) {
  for (const field of data.editable_fields) {
    const value = data.values[field]
    if (BOOLEAN.has(field)) flags[field] = Boolean(value)
    else if (NUMERIC.has(field)) numbers[field] = typeof value === 'number' ? value : null
    else texts[field] = String(value ?? '')
  }
}
async function refresh() {
  const data = await api<SettingsView>('/system/settings')
  view.value = data
  adopt(data)
}
function load() { return run(refresh) }
function save() {
  const payload = changes.value
  if (!view.value || !Object.keys(payload).length) return
  return run(async () => {
    await api('/system/settings', {
      method: 'PUT',
      data: { expected_configuration_id: view.value!.configuration_id, values: payload },
    })
    await refresh()
  }, '配置已生效，检索缓存按新版本隔离')
}
function startRebuild() {
  return run(async () => {
    await api('/system/rebuilds', { method: 'POST', data: rebuildForm })
    rebuildOpen.value = false
    await refresh()
  }, '重建任务已排队')
}
function retry(row: RebuildRow) {
  return run(async () => { await api(`/system/rebuilds/${row.id}/retry`, { method: 'POST' }); await refresh() }, '重建已重新排队')
}
function cancel(row: RebuildRow) {
  dialog.warning({
    title: '取消重建',
    content: `确认取消并删除影子 Collection「${row.target_collection}」？在线索引不受影响。`,
    positiveText: '确认',
    negativeText: '返回',
    onPositiveClick: () => run(async () => { await api(`/system/rebuilds/${row.id}`, { method: 'DELETE' }); await refresh() }, '重建已取消'),
  })
}
function openEvaluate(row: RebuildRow) {
  evaluateForm.rebuildId = row.id
  evaluateOpen.value = true
}
function recordEvaluation() {
  return run(async () => {
    const row = view.value?.rebuilds.find((item) => item.id === evaluateForm.rebuildId)
    if (!row) throw new Error('请选择重建目标')
    await api('/system/evaluations', {
      method: 'POST',
      data: {
        name: evaluateForm.name,
        dataset_version: evaluateForm.datasetVersion,
        dataset_kind: 'frozen',
        dataset_hash: evaluateForm.datasetHash,
        configuration_id: row.target_configuration_id,
        metrics: {
          recall_at_5: evaluateForm.recallAt5,
          mrr: evaluateForm.mrr,
          ndcg_at_10: evaluateForm.ndcgAt10,
          precision_at_5: evaluateForm.precisionAt5,
        },
      },
    })
    evaluateOpen.value = false
    await refresh()
  }, '评估结果已记录')
}
function switchOnline(row: RebuildRow) {
  dialog.warning({
    title: '切换在线索引',
    content: `查询模型与 Collection 将成对切换到「${row.target_collection}」，切换前会校验冻结评估、写入队列与重建期间变更。`,
    positiveText: '确认切换',
    negativeText: '返回',
    onPositiveClick: () => run(async () => { await api(`/system/rebuilds/${row.id}/switch`, { method: 'POST' }); await refresh() }, '在线索引已切换'),
  })
}
const columns: DataTableColumns<RebuildRow> = [
  { title: '影子 Collection', key: 'target_collection', minWidth: 240, ellipsis: { tooltip: true } },
  { title: '状态', key: 'status', width: 100, render: (row) => h(StatusTag, { status: row.status }) },
  { title: '进度', key: 'progress', width: 150, render: (row) => `${row.completed_documents}/${row.total_documents}${row.failed_documents ? ` · 失败 ${row.failed_documents}` : ''}` },
  // A pending rebuild whose update time never moves is the signature of a queue nobody consumes.
  { title: '最近更新', key: 'updated_at', width: 170, render: (row) => formatDate(row.updated_at) },
  { title: '评估', key: 'evaluation_id', width: 80, render: (row) => (row.evaluation_id ? `#${row.evaluation_id}` : '未记录') },
  { title: '说明', key: 'error_message', minWidth: 180, ellipsis: { tooltip: true }, render: (row) => row.error_message ?? '—' },
  {
    title: '操作',
    key: 'actions',
    width: 260,
    render: (row) => h(NSpace, { size: 4 }, () => [
      h(NButton, { size: 'small', quaternary: true, disabled: row.status !== 'failed' || busy.value, onClick: () => retry(row) }, () => '重试'),
      h(NButton, { size: 'small', quaternary: true, disabled: !['evaluating', 'ready'].includes(row.status) || busy.value, onClick: () => openEvaluate(row) }, () => '记录评估'),
      h(NButton, { size: 'small', type: 'primary', quaternary: true, disabled: !['evaluating', 'ready'].includes(row.status) || busy.value, onClick: () => switchOnline(row) }, () => '切换'),
      h(NButton, { size: 'small', quaternary: true, disabled: ['running', 'switched', 'cancelled'].includes(row.status) || busy.value, onClick: () => cancel(row) }, () => '取消'),
    ]),
  },
]
let timer: number | undefined
onMounted(async () => {
  await load()
  timer = window.setInterval(() => {
    if (view.value?.rebuilds.some((row) => ['pending', 'running'].includes(row.status)) && !busy.value) void refresh()
  }, 3000)
})
onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <PageHeader title="系统设置" description="非敏感运行配置存在数据库并对全部实例生效；模型密钥仍只读环境变量。">
    <NSpace>
      <NButton :disabled="Boolean(activeRebuild)" @click="rebuildOpen = true">全量重建索引</NButton>
      <NButton type="primary" :loading="busy" :disabled="!Object.keys(changes).length" @click="save">保存配置</NButton>
    </NSpace>
  </PageHeader>
  <NAlert v-if="view && !view.models_configured" type="warning">模型服务地址或密钥未配置，重建与检索会直接失败。</NAlert>
  <NDescriptions v-if="view" bordered :column="3" size="small" class="identity">
    <NDescriptionsItem label="配置版本">{{ view.configuration_id }}</NDescriptionsItem>
    <NDescriptionsItem label="索引指纹">{{ view.index_fingerprint.slice(0, 16) }}…</NDescriptionsItem>
    <NDescriptionsItem label="在线 Collection">{{ view.values.qdrant_collection }}</NDescriptionsItem>
    <NDescriptionsItem label="Embedding 模型">{{ view.values.embedding_model }}</NDescriptionsItem>
    <NDescriptionsItem label="向量维度">{{ view.values.embedding_dimension }}</NDescriptionsItem>
    <NDescriptionsItem label="分块 / 重叠">{{ view.values.chunk_size }} / {{ view.values.chunk_overlap }}</NDescriptionsItem>
  </NDescriptions>
  <section v-if="view">
    <h3>运行参数</h3>
    <NForm label-placement="left" label-width="180" class="settings-form">
      <NFormItem v-for="field in fields" :key="field" :label="LABELS[field]">
        <NSwitch v-if="BOOLEAN.has(field)" v-model:value="flags[field]" />
        <NInputNumber v-else-if="NUMERIC.has(field)" v-model:value="numbers[field]" :show-arrow="false" />
        <NInput v-else v-model:value="texts[field]" />
      </NFormItem>
    </NForm>
    <p class="muted">模型、维度、分块与分词器决定向量身份，不在这里改：必须先全量重建并通过冻结评估，再成对切换。</p>
  </section>
  <section v-if="view">
    <h3>重建与切换</h3>
    <NDataTable :columns="columns" :data="view.rebuilds" :row-key="(row) => row.id" :bordered="false" size="small" :scroll-x="1060" />
    <p v-if="!view.rebuilds.length" class="muted">尚无重建记录。</p>
  </section>
  <section v-if="view">
    <h3>评估记录</h3>
    <ul class="runs">
      <li v-for="item in view.evaluations" :key="item.id">
        #{{ item.id }} {{ item.name }} · {{ item.dataset_kind }} · {{ item.collection }}
        <NTag :type="item.passed ? 'success' : 'error'" size="small">{{ item.passed ? '达标' : '未达标' }}</NTag>
        <span class="muted">Recall@5 {{ item.metrics.recall_at_5 }} · MRR {{ item.metrics.mrr }} · NDCG@10 {{ item.metrics.ndcg_at_10 }} · P@5 {{ item.metrics.precision_at_5 }}</span>
      </li>
    </ul>
    <p v-if="!view.evaluations.length" class="muted">尚无评估记录。</p>
  </section>
  <NModal v-model:show="rebuildOpen" preset="card" title="全量重建索引" class="modal-form">
    <NAlert type="info">重建会新建影子 Collection，用目标 Embedding 模型重嵌入现有分块，在线检索不受影响。</NAlert>
    <NFormItem label="Embedding 模型"><NInput v-model:value="rebuildForm.embedding_model" placeholder="Qwen/Qwen3-Embedding-4B" /></NFormItem>
    <NFormItem label="向量维度"><NInputNumber v-model:value="rebuildForm.embedding_dimension" :min="1" :max="65536" /></NFormItem>
    <p class="muted">提交时会真实调用模型探测返回维度，维度不符不会开始重建。</p>
    <div class="form-actions">
      <NButton @click="rebuildOpen = false">取消</NButton>
      <NButton type="primary" :loading="busy" :disabled="!rebuildForm.embedding_model.trim()" @click="startRebuild">开始重建</NButton>
    </div>
  </NModal>
  <NModal v-model:show="evaluateOpen" preset="card" title="记录冻结评估" class="modal-form">
    <NFormItem label="评估名称"><NInput v-model:value="evaluateForm.name" placeholder="冻结验收集 2026-09" /></NFormItem>
    <NFormItem label="数据集版本"><NInput v-model:value="evaluateForm.datasetVersion" placeholder="frozen-2026-09" /></NFormItem>
    <NFormItem label="数据集指纹"><NInput v-model:value="evaluateForm.datasetHash" placeholder="64 位十六进制" /></NFormItem>
    <NFormItem label="Recall@5"><NInputNumber v-model:value="evaluateForm.recallAt5" :min="0" :max="1" :step="0.01" /></NFormItem>
    <NFormItem label="MRR"><NInputNumber v-model:value="evaluateForm.mrr" :min="0" :max="1" :step="0.01" /></NFormItem>
    <NFormItem label="NDCG@10"><NInputNumber v-model:value="evaluateForm.ndcgAt10" :min="0" :max="1" :step="0.01" /></NFormItem>
    <NFormItem label="Precision@5"><NInputNumber v-model:value="evaluateForm.precisionAt5" :min="0" :max="1" :step="0.01" /></NFormItem>
    <div class="form-actions">
      <NButton @click="evaluateOpen = false">取消</NButton>
      <NButton type="primary" :loading="busy" @click="recordEvaluation">保存</NButton>
    </div>
  </NModal>
</template>

<style scoped>
.identity { margin-bottom: 24px; }
section { margin-bottom: 32px; }
section h3 { font-size: 15px; margin: 0 0 12px; }
.settings-form { max-width: 620px; }
.runs { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; font-size: 13px; }
</style>
