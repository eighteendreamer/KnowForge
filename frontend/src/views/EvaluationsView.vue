<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NButton, NDataTable } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { api, downloadBlob } from '../api/client'
import { formatDate, useAction } from '../api/feedback'
import type { EvaluationRow } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
const { busy, run } = useAction()
const rows = ref<EvaluationRow[]>([])
const offset = ref(0)
const columns: DataTableColumns<EvaluationRow> = [
  { title: '查询', key: 'query', minWidth: 240 },
  { title: '片段 ID', key: 'chunk_id', width: 220, ellipsis: { tooltip: true } },
  { title: '相关性', key: 'judgment', width: 110, render: (row) => ['不相关', '部分相关', '高度相关'][row.judgment] },
  { title: '标注来源', key: 'judge_type', width: 100 },
  { title: '备注', key: 'notes', minWidth: 180 },
  { title: '标注时间', key: 'created_at', width: 180, render: (row) => formatDate(row.created_at) },
]
function load() { return run(async () => { rows.value = (await api<{ items: EvaluationRow[] }>('/evaluations', { params: { limit: 50, offset: offset.value } })).items }) }
function exportData() { return run(async () => {
  const data = await api<{ items: EvaluationRow[] }>('/evaluations/export')
  downloadBlob(new Blob([JSON.stringify(data.items, null, 2)], { type: 'application/json' }), 'knowforge-evaluations.json')
}, '标注已导出') }
function next(step: number) { offset.value += step * 50; void load() }
onMounted(load)
</script>

<template>
  <PageHeader title="相关性标注" description="人工评估记录，供离线指标计算与检索调优使用。"><NButton :loading="busy" @click="load">刷新</NButton><NButton type="primary" :disabled="busy" @click="exportData">导出 JSON</NButton></PageHeader>
  <NDataTable :data="rows" :columns="columns" :loading="busy" :bordered="false" :row-key="(row) => row.id" :scroll-x="1030" />
  <div class="form-actions"><NButton :disabled="offset === 0 || busy" @click="next(-1)">上一页</NButton><NButton :disabled="rows.length < 50 || busy" @click="next(1)">下一页</NButton></div>
</template>
