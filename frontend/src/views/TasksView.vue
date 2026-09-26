<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NAlert, NButton, NDataTable, NProgress, NSelect } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { api } from '../api/client'
import { useAction, formatDate } from '../api/feedback'
import type { TaskRow } from '../api/types'
import { useTasksStore } from '../stores/tasks'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
const tasks = useTasksStore()
const { busy, run } = useAction()
const filter = ref<string | null>(null)
const data = computed(() => tasks.items.filter((item) => !filter.value || item.status === filter.value))
const typeNames: Record<string, string> = { ingest: '文档入库', reindex: '重新索引', sync_payload: '元数据同步', delete: '删除文档' }
function retry(row: TaskRow) { return run(async () => { await api(`/tasks/${row.id}/retry`, { method: 'POST' }) }, '任务已重新排队') }
const columns: DataTableColumns<TaskRow> = [
  { title: '文档', key: 'title', minWidth: 220, render: (row) => h(RouterLink, { to: `/documents/${row.doc_id}` }, () => row.title) },
  { title: '任务类型', key: 'task_type', width: 120, render: (row) => typeNames[row.task_type] ?? row.task_type },
  { title: '状态', key: 'status', width: 100, render: (row) => h(StatusTag, { status: row.status }) },
  { title: '进度', key: 'progress', width: 160, render: (row) => h(NProgress, { type: 'line', percentage: row.progress, status: row.status === 'failed' ? 'error' : 'success', height: 5 }) },
  { title: '尝试', key: 'attempts', width: 65 },
  { title: '失败原因', key: 'error_message', minWidth: 220, ellipsis: { tooltip: true } },
  { title: '更新时间', key: 'updated_at', width: 180, render: (row) => formatDate(row.updated_at) },
  { title: '操作', key: 'action', width: 80, render: (row) => row.status === 'failed' ? h(NButton, { size: 'small', disabled: busy.value, onClick: () => retry(row) }, () => '重试') : null },
]
</script>

<template>
  <PageHeader title="处理任务" description="最近 100 条任务，进度由服务端实时推送。">
    <span class="muted">{{ tasks.connected ? '实时连接已建立' : '正在连接…' }}</span>
  </PageHeader>
  <NAlert v-if="tasks.error" type="warning" style="margin-bottom: 20px">{{ tasks.error }}，正在自动重连。</NAlert>
  <div class="toolbar"><NSelect v-model:value="filter" clearable placeholder="全部状态" :options="[{ label: '等待处理', value: 'pending' }, { label: '处理中', value: 'running' }, { label: '已完成', value: 'succeeded' }, { label: '失败', value: 'failed' }]" /></div>
  <NDataTable :columns="columns" :data="data" :row-key="(row) => row.id" :bordered="false" :scroll-x="1160" :pagination="{ pageSize: 20 }" />
</template>
