<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NDataTable, NDatePicker, NSelect, NTag, type DataTableColumns } from 'naive-ui'
import { api } from '../api/client'
import { formatDate, useAction } from '../api/feedback'
import type { ApiKeyRow, ApiLogRow, Page } from '../api/types'
import { useNarrow } from '../composables/media'
import PageHeader from '../components/PageHeader.vue'

const route = useRoute()
const { busy, run } = useAction()
const narrow = useNarrow()
const rows = ref<ApiLogRow[]>([])
const keys = ref<ApiKeyRow[]>([])
const filters = reactive<{ key_id: number | null; status_code: number | null; range: [number, number] | null }>({
  key_id: null,
  status_code: null,
  range: null,
})
const pagination = reactive({ page: 1, pageSize: 50, itemCount: 0 })

const keyOptions = () => keys.value.map((row) => ({ label: `${row.name}（${row.key_prefix}）`, value: row.id }))
const statusOptions = [200, 400, 401, 403, 429, 500, 503].map((code) => ({ label: String(code), value: code }))

function day(value: number) {
  return new Date(value).toISOString().slice(0, 10)
}

async function fetchRows() {
  const params: Record<string, string | number> = {
    limit: pagination.pageSize,
    offset: (pagination.page - 1) * pagination.pageSize,
  }
  if (filters.key_id !== null) params.key_id = filters.key_id
  if (filters.status_code !== null) params.status_code = filters.status_code
  if (filters.range) {
    params.date_from = day(filters.range[0])
    params.date_to = day(filters.range[1])
  }
  const data = await api<Page<ApiLogRow>>('/usage', { params })
  rows.value = data.items
  pagination.itemCount = data.total
}
function load() {
  return run(fetchRows)
}
function reload() {
  pagination.page = 1
  return load()
}
function changePage(page: number, pageSize: number) {
  pagination.page = page
  pagination.pageSize = pageSize
  void load()
}
watch(
  () => [filters.key_id, filters.status_code, filters.range],
  () => reload(),
  { deep: true }
)

function statusType(code: number | null) {
  if (code === null) return 'default'
  if (code < 300) return 'success'
  if (code < 500) return 'warning'
  return 'error'
}

const columns = computed<DataTableColumns<ApiLogRow>>(() => {
  if (narrow.value) {
    // 窄屏把"哪次调用"合成一格：时间一行、接口一行，右边只留状态与耗时。
    return [
      {
        title: '调用',
        key: 'created_at',
        render: (row) =>
          h('div', { class: 'cell-stacked' }, [
            h('span', null, formatDate(row.created_at)),
            h('span', { class: 'cell-sub' }, `${row.method} ${row.endpoint}${row.query ? ` · ${row.query}` : ''}`),
          ]),
      },
      {
        title: '结果',
        key: 'status_code',
        width: 96,
        render: (row) =>
          h('div', { class: 'cell-stacked' }, [
            h(
              NTag,
              { size: 'small', bordered: false, type: statusType(row.status_code) },
              () => String(row.status_code ?? '—')
            ),
            h('span', { class: 'cell-sub' }, row.latency_ms === null ? '—' : `${row.latency_ms} ms`),
          ]),
      },
    ]
  }
  return [
    {
      title: '时间',
      key: 'created_at',
      width: 172,
      render: (row) => formatDate(row.created_at),
    },
    { title: '接口', key: 'endpoint', minWidth: 190 },
    { title: '方法', key: 'method', width: 76 },
    { title: '查询词', key: 'query', minWidth: 200, render: (row) => row.query ?? '—' },
    {
      title: '模式',
      key: 'search_type',
      width: 96,
      render: (row) => row.search_type ?? '—',
    },
    {
      title: '命中',
      key: 'result_count',
      width: 84,
      render: (row) => (row.result_count === null ? '—' : row.result_count),
    },
    {
      title: '耗时',
      key: 'latency_ms',
      width: 92,
      render: (row) => (row.latency_ms === null ? '—' : `${row.latency_ms} ms`),
    },
    {
      title: '状态',
      key: 'status_code',
      width: 92,
      render: (row) => h(NTag, { size: 'small', bordered: false, type: statusType(row.status_code) }, () => String(row.status_code ?? '—')),
    },
  ]
})

onMounted(async () => {
  const queryKey = Number(route.query.key_id)
  if (Number.isInteger(queryKey) && queryKey > 0) filters.key_id = queryKey
  await run(async () => {
    keys.value = (await api<Page<ApiKeyRow>>('/keys')).items
    await fetchRows()
  })
})
</script>

<template>
  <PageHeader title="调用记录" description="每次对外检索调用都会留痕：接口、生效模式、命中数、耗时与状态码。">
    <NButton size="small" :loading="busy" @click="load">刷新</NButton>
  </PageHeader>
  <div class="toolbar">
    <NSelect v-model:value="filters.key_id" placeholder="全部密钥" clearable :options="keyOptions()" class="toolbar-key" />
    <NSelect v-model:value="filters.status_code" placeholder="全部状态码" clearable :options="statusOptions" />
    <NDatePicker v-model:value="filters.range" type="daterange" clearable />
    <span class="muted toolbar-note">共 {{ pagination.itemCount }} 条 · 配额是频控，与账户余额无关</span>
  </div>
  <NDataTable
    :columns="columns"
    :data="rows"
    :loading="busy"
    remote
    :row-key="(row) => row.id"
    :bordered="false"
    :scroll-x="narrow ? undefined : 1100"
    :pagination="{
      page: pagination.page,
      pageSize: pagination.pageSize,
      itemCount: pagination.itemCount,
      showSizePicker: true,
      pageSizes: [20, 50, 100],
      onChange: (page: number) => changePage(page, pagination.pageSize),
      onUpdatePageSize: (pageSize: number) => changePage(1, pageSize),
    }"
  />
</template>
