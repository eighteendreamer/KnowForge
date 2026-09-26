<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NButton, NDataTable } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { api } from '../api/client'
import { formatDate, useAction } from '../api/feedback'
import type { AuditRow } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
const { busy, run } = useAction()
const rows = ref<AuditRow[]>([])
const offset = ref(0)
const columns: DataTableColumns<AuditRow> = [
  { title: '时间', key: 'created_at', width: 180, render: (row) => formatDate(row.created_at) },
  { title: '操作人 ID', key: 'operator_id', width: 100 },
  { title: '来源 IP', key: 'client_ip', width: 130, render: (row) => row.client_ip ?? '—' },
  { title: '操作', key: 'action', width: 130 },
  { title: '对象类型', key: 'target_type', width: 140 },
  { title: '对象 ID', key: 'target_id', width: 240, ellipsis: { tooltip: true } },
  { title: '详情', key: 'details', minWidth: 200, render: (row) => row.details ? JSON.stringify(row.details) : '—', ellipsis: { tooltip: true } },
]
function load() { return run(async () => { rows.value = (await api<{ items: AuditRow[] }>('/audit-logs', { params: { limit: 50, offset: offset.value } })).items }) }
function next(step: number) { offset.value += step * 50; void load() }
onMounted(load)
</script>

<template>
  <PageHeader title="审计日志" description="管理员操作记录，仅超级管理员可见。"><NButton :loading="busy" @click="load">刷新</NButton></PageHeader>
  <NDataTable :columns="columns" :data="rows" :loading="busy" :row-key="(row) => row.id" :bordered="false" :scroll-x="1080" />
  <div class="form-actions"><NButton :disabled="offset === 0 || busy" @click="next(-1)">上一页</NButton><NButton :disabled="rows.length < 50 || busy" @click="next(1)">下一页</NButton></div>
</template>
