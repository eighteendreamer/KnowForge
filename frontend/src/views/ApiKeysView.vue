<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NDatePicker,
  NDataTable,
  NDrawer,
  NDrawerContent,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NTag,
  useDialog,
  type DataTableColumns,
} from 'naive-ui'
import { api } from '../api/client'
import { formatDate, useAction } from '../api/feedback'
import type { ApiKeyRow, ApiLogRow, KeyOwnerRow, Page } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'

const { busy, run } = useAction()
const dialog = useDialog()
const rows = ref<ApiKeyRow[]>([])
const owners = ref<KeyOwnerRow[]>([])
const filters = reactive<{ owner_id: number | null; q: string }>({ owner_id: null, q: '' })
const open = ref(false)
const editing = ref<number | null>(null)
const plaintext = ref('')
const secretOpen = ref(false)
const form = reactive({
  name: '',
  rate_limit_per_day: 1000,
  rate_limit_per_minute: 60,
  expires_at: null as number | null,
})

const logsOpen = ref(false)
const logs = ref<ApiLogRow[]>([])
const logTotal = ref(0)
const logFilter = reactive<{ key_id: number | null; status_code: number | null; range: [number, number] | null }>({
  key_id: null,
  status_code: null,
  range: null,
})
const logPage = reactive({ page: 1, pageSize: 50, itemCount: 0 })

async function fetchRows() {
  const params: Record<string, string | number> = { limit: 200 }
  if (filters.q) params.q = filters.q
  if (filters.owner_id !== null) params.owner_id = filters.owner_id
  rows.value = (await api<Page<ApiKeyRow>>('/api-keys', { params })).items
}
function load() {
  return run(async () => {
    await fetchRows()
    owners.value = (await api<Page<KeyOwnerRow>>('/api-keys/accounts')).items
  })
}
function edit(row?: ApiKeyRow) {
  editing.value = row?.id ?? null
  form.name = row?.name ?? ''
  form.rate_limit_per_day = row?.rate_limit_per_day ?? 1000
  form.rate_limit_per_minute = row?.rate_limit_per_minute ?? 60
  form.expires_at = row?.expires_at ? new Date(row.expires_at).getTime() : null
  open.value = true
}
function save() {
  return run(async () => {
    const data = { ...form, expires_at: form.expires_at === null ? null : new Date(form.expires_at).toISOString() }
    const response = await api<ApiKeyRow & { key?: string }>(editing.value ? `/api-keys/${editing.value}` : '/api-keys', {
      method: editing.value ? 'PATCH' : 'POST',
      data,
    })
    open.value = false
    if (response.key) {
      plaintext.value = response.key
      secretOpen.value = true
    }
    await fetchRows()
  }, '密钥配置已保存')
}
function toggle(row: ApiKeyRow) {
  return run(
    async () => {
      await api(`/api-keys/${row.id}`, { method: 'PATCH', data: { status: row.status === 'active' ? 'disabled' : 'active' } })
      await fetchRows()
    },
    '密钥状态已更新'
  )
}
function revoke(row: ApiKeyRow) {
  dialog.warning({
    title: '吊销 API 密钥',
    content: `“${row.name}”吊销后不可恢复，已有客户端将无法继续调用。`,
    positiveText: '吊销',
    negativeText: '取消',
    onPositiveClick: () =>
      run(async () => {
        await api(`/api-keys/${row.id}`, { method: 'DELETE' })
        await fetchRows()
      }, '密钥已吊销'),
  })
}

function day(value: number) {
  return new Date(value).toISOString().slice(0, 10)
}
async function fetchLogs() {
  const params: Record<string, string | number> = {
    limit: logPage.pageSize,
    offset: (logPage.page - 1) * logPage.pageSize,
  }
  if (logFilter.key_id !== null) params.key_id = logFilter.key_id
  if (logFilter.status_code !== null) params.status_code = logFilter.status_code
  if (logFilter.range) {
    params.date_from = day(logFilter.range[0])
    params.date_to = day(logFilter.range[1])
  }
  const data = await api<Page<ApiLogRow>>('/api-keys/logs', { params })
  logs.value = data.items
  logTotal.value = data.total
  logPage.itemCount = data.total
}
function showLogs(keyId: number | null) {
  logFilter.key_id = keyId
  logFilter.status_code = null
  logFilter.range = null
  logPage.page = 1
  return run(async () => {
    await fetchLogs()
    logsOpen.value = true
  })
}
function reloadLogs() {
  logPage.page = 1
  return run(fetchLogs)
}
function changeLogPage(page: number, pageSize: number) {
  logPage.page = page
  logPage.pageSize = pageSize
  void run(fetchLogs)
}

const columns: DataTableColumns<ApiKeyRow> = [
  { title: '名称', key: 'name', minWidth: 150 },
  { title: '密钥前缀', key: 'key_prefix', width: 140 },
  {
    title: '所属账号',
    key: 'owner_username',
    width: 170,
    render: (row) =>
      row.owner_username
        ? h(NSpace, { size: 6 }, () => [
            h('span', {}, row.owner_username ?? ''),
            h(NTag, { size: 'small', bordered: false }, () => (row.owner_role === 'end_user' ? '门户' : '管理')),
          ])
        : h('span', { class: 'muted' }, '未归属'),
  },
  { title: '状态', key: 'status', width: 96, render: (row) => h(StatusTag, { status: row.status }) },
  {
    title: '日 / 分钟限额',
    key: 'rate_limit_per_day',
    width: 140,
    render: (row) => `${row.rate_limit_per_day} / ${row.rate_limit_per_minute}`,
  },
  { title: '累计调用', key: 'total_calls', width: 100 },
  {
    title: '到期时间',
    key: 'expires_at',
    width: 176,
    render: (row) => (row.expires_at ? formatDate(row.expires_at) : '永久有效'),
  },
  {
    title: '操作',
    key: 'actions',
    width: 250,
    render: (row) =>
      h(NSpace, { size: 4 }, () => [
        h(NButton, { size: 'small', quaternary: true, onClick: () => showLogs(row.id) }, () => '请求'),
        ...(row.status !== 'revoked'
          ? [
              h(NButton, { size: 'small', quaternary: true, onClick: () => edit(row) }, () => '编辑'),
              h(
                NButton,
                { size: 'small', quaternary: true, disabled: busy.value, onClick: () => toggle(row) },
                () => (row.status === 'active' ? '停用' : '启用')
              ),
              h(NButton, { size: 'small', quaternary: true, type: 'error', onClick: () => revoke(row) }, () => '吊销'),
            ]
          : []),
      ]),
  },
]

const logColumns: DataTableColumns<ApiLogRow> = [
  { title: '时间', key: 'created_at', width: 175, render: (row) => formatDate(row.created_at) },
  { title: '所属账号', key: 'owner_username', width: 140, render: (row) => row.owner_username ?? '未归属' },
  { title: '密钥', key: 'key_name', width: 130, render: (row) => row.key_name ?? '—' },
  { title: '接口', key: 'endpoint', minWidth: 180 },
  { title: '查询', key: 'query', minWidth: 160, render: (row) => row.query ?? '—' },
  { title: '模式', key: 'search_type', width: 90, render: (row) => row.search_type ?? '—' },
  { title: '命中', key: 'result_count', width: 80, render: (row) => row.result_count ?? '—' },
  { title: 'HTTP', key: 'status_code', width: 76 },
  { title: '耗时 ms', key: 'latency_ms', width: 90 },
]

function copyKey() {
  return run(async () => {
    await navigator.clipboard.writeText(plaintext.value)
  }, '密钥已复制')
}

const ownerOptions = () => owners.value.map((row) => ({ label: `${row.username}（${row.keys} 把）`, value: row.id }))
const statusOptions = [200, 400, 401, 403, 429, 500, 503].map((code) => ({ label: String(code), value: code }))

onMounted(load)
</script>

<template>
  <PageHeader title="API 密钥" description="全部用户的只读凭据与调用额度；“请求”可查看该密钥的调用流水。">
    <div class="row">
      <NButton @click="showLogs(null)">全部请求</NButton>
      <NButton type="primary" @click="edit()">创建密钥</NButton>
    </div>
  </PageHeader>
  <div class="toolbar">
    <NInput v-model:value="filters.q" clearable placeholder="按密钥名称搜索" @update:value="run(fetchRows)" />
    <NSelect
      v-model:value="filters.owner_id"
      placeholder="全部账号"
      clearable
      :options="ownerOptions()"
      style="width: 240px"
      @update:value="run(fetchRows)"
    />
    <NButton :loading="busy" @click="load">刷新</NButton>
    <span class="muted">共 {{ rows.length }} 把密钥</span>
  </div>
  <NDataTable
    :columns="columns"
    :data="rows"
    :loading="busy"
    :row-key="(row) => row.id"
    :bordered="false"
    :scroll-x="1220"
    :pagination="{ pageSize: 20 }"
  />
  <NModal v-model:show="open" preset="card" :title="editing ? '编辑密钥' : '创建密钥'" class="modal-form">
    <NFormItem label="名称"><NInput v-model:value="form.name" :maxlength="100" /></NFormItem>
    <NFormItem label="每日调用上限">
      <NInputNumber v-model:value="form.rate_limit_per_day" :min="1" :max="10000000" :precision="0" />
    </NFormItem>
    <NFormItem label="每分钟调用上限">
      <NInputNumber v-model:value="form.rate_limit_per_minute" :min="1" :max="100000" :precision="0" />
    </NFormItem>
    <NFormItem label="到期时间（留空永久有效）"><NDatePicker v-model:value="form.expires_at" type="datetime" clearable /></NFormItem>
    <div class="form-actions">
      <NButton @click="open = false">取消</NButton>
      <NButton type="primary" :loading="busy" :disabled="!form.name.trim()" @click="save">保存</NButton>
    </div>
  </NModal>
  <NModal v-model:show="secretOpen" preset="card" title="保存你的 API 密钥" class="modal-form" @after-leave="plaintext = ''">
    <NAlert type="warning" style="margin-bottom: 20px">密钥明文仅展示本次。关闭后无法再次查看，请妥善保存。</NAlert>
    <NInput :value="plaintext" readonly type="textarea" :autosize="{ minRows: 2 }" />
    <div class="form-actions">
      <NButton type="primary" @click="copyKey">复制密钥</NButton>
      <NButton @click="secretOpen = false">已保存，关闭</NButton>
    </div>
  </NModal>
  <NDrawer v-model:show="logsOpen" :width="1100">
    <NDrawerContent :title="logFilter.key_id ? '密钥调用流水' : '全部用户调用流水'" closable>
      <div class="toolbar">
        <NSelect
          v-model:value="logFilter.status_code"
          placeholder="全部状态码"
          clearable
          :options="statusOptions"
          @update:value="reloadLogs"
        />
        <NDatePicker v-model:value="logFilter.range" type="daterange" clearable @update:value="reloadLogs" />
        <NButton :loading="busy" @click="reloadLogs">刷新</NButton>
        <span class="muted">共 {{ logTotal }} 条</span>
      </div>
      <NDataTable
        :columns="logColumns"
        :data="logs"
        :loading="busy"
        remote
        :row-key="(row) => row.id"
        :bordered="false"
        :scroll-x="1180"
        :pagination="{
          page: logPage.page,
          pageSize: logPage.pageSize,
          itemCount: logPage.itemCount,
          showSizePicker: true,
          pageSizes: [50, 100, 200],
          onChange: (page: number) => changeLogPage(page, logPage.pageSize),
          onUpdatePageSize: (pageSize: number) => changeLogPage(1, pageSize),
        }"
      />
    </NDrawerContent>
  </NDrawer>
</template>
