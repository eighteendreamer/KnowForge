<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  NAlert,
  NButton,
  NDataTable,
  NDatePicker,
  NDropdown,
  NFormItem,
  NInput,
  NModal,
  useDialog,
  type DataTableColumns,
} from 'naive-ui'
import { api, setPlaygroundKey } from '../api/client'
import { formatDate, useAction } from '../api/feedback'
import type { ApiKeyRow, Page } from '../api/types'
import { useNarrow } from '../composables/media'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'

const { busy, run } = useAction()
const dialog = useDialog()
const narrow = useNarrow()
const rows = ref<ApiKeyRow[]>([])
const open = ref(false)
const editing = ref<number | null>(null)
const form = reactive({ name: '', expires_at: null as number | null })
const plaintext = ref('')
const secretOpen = ref(false)

async function fetchRows() {
  rows.value = (await api<Page<ApiKeyRow>>('/keys')).items
}
function load() {
  return run(fetchRows)
}
function edit(row?: ApiKeyRow) {
  editing.value = row?.id ?? null
  form.name = row?.name ?? ''
  form.expires_at = row?.expires_at ? new Date(row.expires_at).getTime() : null
  open.value = true
}
function save() {
  return run(async () => {
    const data = { name: form.name, expires_at: form.expires_at === null ? null : new Date(form.expires_at).toISOString() }
    const response = await api<ApiKeyRow & { key?: string }>(editing.value ? `/keys/${editing.value}` : '/keys', {
      method: editing.value ? 'PATCH' : 'POST',
      data,
    })
    open.value = false
    if (response.key) {
      plaintext.value = response.key
      secretOpen.value = true
    }
    await fetchRows()
  }, '密钥已保存')
}
function toggle(row: ApiKeyRow) {
  return run(
    async () => {
      await api(`/keys/${row.id}`, { method: 'PATCH', data: { status: row.status === 'active' ? 'disabled' : 'active' } })
      await fetchRows()
    },
    '密钥状态已更新'
  )
}
function revoke(row: ApiKeyRow) {
  dialog.warning({
    title: '吊销 API Key',
    content: `“${row.name}”吊销后不可恢复，已接入的客户端会立即收到 401。`,
    positiveText: '吊销',
    negativeText: '取消',
    onPositiveClick: () =>
      run(async () => {
        await api(`/keys/${row.id}`, { method: 'DELETE' })
        await fetchRows()
      }, '密钥已吊销'),
  })
}
function copyKey() {
  return run(async () => {
    await navigator.clipboard.writeText(plaintext.value)
  }, '密钥已复制')
}
function useInPlayground() {
  setPlaygroundKey(plaintext.value)
}
function rowMenu(row: ApiKeyRow) {
  if (row.status === 'revoked') return []
  return [
    { key: 'edit', label: '编辑' },
    { key: 'toggle', label: row.status === 'active' ? '停用' : '启用' },
    { key: 'revoke', label: '吊销', props: { style: 'color:#d03050' } },
  ]
}

function onRowMenu(key: string, row: ApiKeyRow) {
  if (key === 'edit') edit(row)
  else if (key === 'toggle') void toggle(row)
  else if (key === 'revoke') revoke(row)
}

// 一行里只放"一个链接 + 一个更多菜单"，四个文字按钮并排会在窄列里换行错位。
function actionsCell(row: ApiKeyRow) {
  const link = h(
    RouterLink,
    { to: { path: '/console/usage', query: { key_id: String(row.id) } } },
    () => h(NButton, { size: 'small', quaternary: true }, () => '调用记录')
  )
  const menu = rowMenu(row)
  if (!menu.length) return h('div', { class: 'row-actions' }, [link])
  return h('div', { class: 'row-actions' }, [
    link,
    h(
      NDropdown,
      { trigger: 'click', options: menu, onSelect: (key: string) => onRowMenu(key, row) },
      () => h(NButton, { size: 'small', quaternary: true, disabled: busy.value }, () => '更多')
    ),
  ])
}

const columns = computed<DataTableColumns<ApiKeyRow>>(() => {
  if (narrow.value) {
    // 窄屏只留能决策的三列：谁、什么状态、能做什么。配额与时间戳收进名称下的第二行。
    return [
      {
        title: '密钥',
        key: 'name',
        render: (row) =>
          h('div', { class: 'cell-stacked' }, [
            h('strong', null, row.name),
            h('span', { class: 'cell-sub' }, `${row.key_prefix} · ${row.rate_limit_per_minute}/分 · 累计 ${row.total_calls}`),
          ]),
      },
      { title: '状态', key: 'status', width: 78, render: (row) => h(StatusTag, { status: row.status }) },
      { title: '操作', key: 'actions', width: 132, render: actionsCell },
    ]
  }
  return [
    { title: '名称', key: 'name', minWidth: 150 },
    { title: '密钥前缀', key: 'key_prefix', width: 140 },
    { title: '状态', key: 'status', width: 96, render: (row) => h(StatusTag, { status: row.status }) },
    {
      title: '配额（分钟 / 日）',
      key: 'rate_limit_per_minute',
      width: 160,
      render: (row) => `${row.rate_limit_per_minute} / ${row.rate_limit_per_day}`,
    },
    { title: '累计调用', key: 'total_calls', width: 100 },
    {
      title: '到期时间',
      key: 'expires_at',
      width: 176,
      render: (row) => (row.expires_at ? formatDate(row.expires_at) : '永久有效'),
    },
    {
      title: '最后使用',
      key: 'last_used_at',
      width: 176,
      render: (row) => (row.last_used_at ? formatDate(row.last_used_at) : '尚未使用'),
    },
    { title: '操作', key: 'actions', width: 190, render: actionsCell },
  ]
})
onMounted(load)
</script>

<template>
  <PageHeader title="API Key" description="只读检索凭据。配额由平台设定，需要调整请在后台申请。">
    <NButton type="primary" @click="edit()">创建密钥</NButton>
  </PageHeader>
  <NDataTable
    :columns="columns"
    :data="rows"
    :loading="busy"
    :row-key="(row) => row.id"
    :bordered="false"
    :single-line="false"
    :scroll-x="narrow ? undefined : 1120"
    :pagination="{ pageSize: narrow ? 10 : 20 }"
  />
  <NModal v-model:show="open" preset="card" :title="editing ? '编辑密钥' : '创建密钥'" class="modal-form">
    <NFormItem label="名称">
      <NInput v-model:value="form.name" :maxlength="100" placeholder="例如 生产环境客服机器人" />
    </NFormItem>
    <NFormItem label="到期时间（留空永久有效）">
      <NDatePicker v-model:value="form.expires_at" type="datetime" clearable />
    </NFormItem>
    <div class="form-actions">
      <NButton @click="open = false">取消</NButton>
      <NButton type="primary" :loading="busy" :disabled="!form.name.trim()" @click="save">保存</NButton>
    </div>
  </NModal>
  <NModal v-model:show="secretOpen" preset="card" title="保存你的 API Key" class="modal-form" @after-leave="plaintext = ''">
    <NAlert type="warning" style="margin-bottom: 20px">
      密钥明文只显示这一次。服务端只保存 SHA-256 摘要，关闭后无法找回，只能重新创建。
    </NAlert>
    <NInput :value="plaintext" readonly type="textarea" :autosize="{ minRows: 2 }" class="secret-box" />
    <div class="form-actions">
      <NButton tertiary @click="useInPlayground">填入检索试用</NButton>
      <NButton @click="copyKey">复制密钥</NButton>
      <NButton type="primary" @click="secretOpen = false">已保存，关闭</NButton>
    </div>
  </NModal>
</template>
