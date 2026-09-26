<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import {
  NButton,
  NDataTable,
  NDrawer,
  NDrawerContent,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  useDialog,
  type DataTableColumns,
} from 'naive-ui'
import { api } from '../api/client'
import { formatCent, formatDate, toCent, useAction } from '../api/feedback'
import type { AccountRole, Page, TransactionRow, User } from '../api/types'
import { useAuthStore } from '../stores/auth'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'

const auth = useAuthStore()
const { busy, run } = useAction()
const dialog = useDialog()
const rows = ref<User[]>([])
const total = ref(0)
const filters = reactive({ q: '', role: null as AccountRole | null, status: null as 'active' | 'disabled' | null })
const pagination = reactive({ page: 1, pageSize: 50, itemCount: 0 })
const open = ref(false)
const editing = ref<User | null>(null)
const form = reactive({ username: '', password: '', role: 'content_admin' as AccountRole })
const rechargeOpen = ref(false)
const rechargeTarget = ref<User | null>(null)
const rechargeForm = reactive({ yuan: 100, note: '' })
const ledgerOpen = ref(false)
const ledger = ref<TransactionRow[]>([])
const ledgerTarget = ref<User | null>(null)

const roleLabels: Record<AccountRole, string> = {
  super_admin: '超级管理员',
  content_admin: '内容管理员',
  end_user: '门户用户',
}

async function fetchRows() {
  const params: Record<string, string | number> = {
    limit: pagination.pageSize,
    offset: (pagination.page - 1) * pagination.pageSize,
  }
  if (filters.q) params.q = filters.q
  if (filters.role) params.role = filters.role
  if (filters.status) params.status = filters.status
  const data = await api<Page<User>>('/users', { params })
  rows.value = data.items
  total.value = data.total
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
function edit(row?: User) {
  editing.value = row ?? null
  form.username = row?.username ?? ''
  form.password = ''
  form.role = row?.role ?? 'content_admin'
  open.value = true
}
function save() {
  return run(async () => {
    const data = editing.value ? { role: form.role, ...(form.password ? { password: form.password } : {}) } : form
    await api(editing.value ? `/users/${editing.value.id}` : '/users', {
      method: editing.value ? 'PATCH' : 'POST',
      data,
    })
    open.value = false
    form.password = ''
    await fetchRows()
  }, '账号已保存')
}
function toggle(row: User) {
  const status = row.status === 'active' ? 'disabled' : 'active'
  dialog.warning({
    title: status === 'disabled' ? '停用账号' : '启用账号',
    content: `确认${status === 'disabled' ? '停用' : '启用'}“${row.username}”？`,
    positiveText: '确认',
    negativeText: '取消',
    onPositiveClick: () =>
      run(async () => {
        await api(`/users/${row.id}`, { method: 'PATCH', data: { status } })
        await fetchRows()
      }, '状态已更新'),
  })
}
function recharge(row: User) {
  rechargeTarget.value = row
  rechargeForm.yuan = 100
  rechargeForm.note = ''
  rechargeOpen.value = true
}
function submitRecharge() {
  const cent = toCent(rechargeForm.yuan)
  return run(async () => {
    await api(`/users/${rechargeTarget.value?.id}/recharge`, {
      method: 'POST',
      data: { amount_cent: cent, note: rechargeForm.note },
    })
    rechargeOpen.value = false
    await fetchRows()
  }, `已充值 ${formatCent(cent)}`)
}
function showLedger(row: User) {
  ledgerTarget.value = row
  return run(async () => {
    ledger.value = (await api<Page<TransactionRow>>(`/users/${row.id}/transactions`)).items
    ledgerOpen.value = true
  })
}

const columns: DataTableColumns<User> = [
  { title: '用户名', key: 'username', minWidth: 160 },
  { title: '角色', key: 'role', width: 130, render: (row) => roleLabels[row.role] ?? row.role },
  { title: '状态', key: 'status', width: 100, render: (row) => h(StatusTag, { status: row.status }) },
  {
    title: '余额',
    key: 'balance_cent',
    width: 130,
    render: (row) => formatCent(row.balance_cent ?? 0),
  },
  {
    title: '注册时间',
    key: 'created_at',
    width: 180,
    render: (row) => (row.created_at ? formatDate(row.created_at) : '—'),
  },
  {
    title: '操作',
    key: 'actions',
    width: 300,
    render: (row) =>
      h(NSpace, { size: 4 }, () => [
        h(NButton, { size: 'small', quaternary: true, onClick: () => edit(row) }, () => '编辑'),
        h(NButton, { size: 'small', quaternary: true, onClick: () => recharge(row) }, () => '充值'),
        h(NButton, { size: 'small', quaternary: true, onClick: () => showLedger(row) }, () => '流水'),
        h(
          NButton,
          {
            size: 'small',
            quaternary: true,
            disabled: row.id === auth.user?.id || busy.value,
            onClick: () => toggle(row),
          },
          () => (row.status === 'active' ? '停用' : '启用')
        ),
      ]),
  },
]

const ledgerColumns: DataTableColumns<TransactionRow> = [
  { title: '时间', key: 'created_at', width: 180, render: (row) => formatDate(row.created_at) },
  { title: '入账金额', key: 'amount_cent', width: 130, render: (row) => formatCent(row.amount_cent) },
  { title: '渠道', key: 'channel', width: 100 },
  { title: '备注', key: 'note', minWidth: 180, render: (row) => row.note ?? '—' },
]

onMounted(load)
</script>

<template>
  <PageHeader title="用户管理" description="管理员与门户用户共用一套账号；余额由这里手动入账。">
    <NButton type="primary" @click="edit()">创建账号</NButton>
  </PageHeader>
  <div class="toolbar">
    <NInput v-model:value="filters.q" clearable placeholder="按用户名搜索" @update:value="reload" />
    <NSelect
      v-model:value="filters.role"
      placeholder="全部角色"
      clearable
      :options="[
        { label: '超级管理员', value: 'super_admin' },
        { label: '内容管理员', value: 'content_admin' },
        { label: '门户用户', value: 'end_user' },
      ]"
      @update:value="reload"
    />
    <NSelect
      v-model:value="filters.status"
      placeholder="全部状态"
      clearable
      :options="[
        { label: '启用', value: 'active' },
        { label: '停用', value: 'disabled' },
      ]"
      @update:value="reload"
    />
    <NButton :loading="busy" @click="load">刷新</NButton>
    <span class="muted">共 {{ total }} 个账号</span>
  </div>
  <NDataTable
    :columns="columns"
    :data="rows"
    :loading="busy"
    remote
    :row-key="(row) => row.id"
    :bordered="false"
    :scroll-x="1050"
    :pagination="{
      page: pagination.page,
      pageSize: pagination.pageSize,
      itemCount: pagination.itemCount,
      showSizePicker: true,
      pageSizes: [50, 100, 200],
      onChange: (page: number) => changePage(page, pagination.pageSize),
      onUpdatePageSize: (pageSize: number) => changePage(1, pageSize),
    }"
  />
  <NModal v-model:show="open" preset="card" :title="editing ? '编辑账号' : '创建账号'" class="modal-form">
    <NFormItem label="用户名">
      <NInput v-model:value="form.username" :disabled="!!editing" :maxlength="100" :input-props="{ autocomplete: 'off' }" />
    </NFormItem>
    <NFormItem :label="editing ? '重置密码（留空不修改）' : '密码（至少 12 位）'">
      <NInput
        v-model:value="form.password"
        type="password"
        show-password-on="click"
        :input-props="{ autocomplete: 'new-password' }"
        :maxlength="256"
      />
    </NFormItem>
    <NFormItem label="角色">
      <NSelect
        v-model:value="form.role"
        :disabled="editing?.id === auth.user?.id"
        :options="[
          { label: '超级管理员', value: 'super_admin' },
          { label: '内容管理员', value: 'content_admin' },
          { label: '门户用户', value: 'end_user' },
        ]"
      />
    </NFormItem>
    <div class="form-actions">
      <NButton @click="open = false">取消</NButton>
      <NButton
        type="primary"
        :loading="busy"
        :disabled="!form.username.trim() || (!editing && form.password.length < 12) || (!!form.password && form.password.length < 12)"
        @click="save"
      >
        保存
      </NButton>
    </div>
  </NModal>
  <NModal v-model:show="rechargeOpen" preset="card" :title="`为 ${rechargeTarget?.username ?? ''} 充值`" class="modal-form">
    <NFormItem label="充值金额（元）">
      <NInputNumber v-model:value="rechargeForm.yuan" :min="0.01" :max="1000000" :precision="2" style="width: 100%" />
    </NFormItem>
    <NFormItem label="入账备注（必填，写入审计）">
      <NInput v-model:value="rechargeForm.note" :maxlength="200" placeholder="例如 线下对公转账 2026-09-26" />
    </NFormItem>
    <p class="muted" style="font-size: 12px">模拟充值：直接记入账本，不影响调用配额，也不触发任何支付渠道。</p>
    <div class="form-actions">
      <NButton @click="rechargeOpen = false">取消</NButton>
      <NButton
        type="primary"
        :loading="busy"
        :disabled="!rechargeForm.note.trim() || !rechargeForm.yuan || rechargeForm.yuan <= 0"
        @click="submitRecharge"
      >
        确认入账
      </NButton>
    </div>
  </NModal>
  <NDrawer v-model:show="ledgerOpen" :width="720">
    <NDrawerContent :title="`${ledgerTarget?.username ?? ''} · 入账流水`" closable>
      <NDataTable :columns="ledgerColumns" :data="ledger" :bordered="false" :max-height="520" />
    </NDrawerContent>
  </NDrawer>
</template>
