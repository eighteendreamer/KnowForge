<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NDataTable,
  NEmpty,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSwitch,
  NSpace,
  useDialog,
  type DataTableColumns,
} from 'naive-ui'
import { api } from '../api/client'
import { formatCent, formatDate, toCent, useAction } from '../api/feedback'
import type { Page, RechargeChannelRow, RechargePackageRow } from '../api/types'
import PageHeader from '../components/PageHeader.vue'

const { busy, run } = useAction()
const dialog = useDialog()
const packages = ref<RechargePackageRow[]>([])
const channels = ref<RechargeChannelRow[]>([])

const packageOpen = ref(false)
const packageEditing = ref<RechargePackageRow | null>(null)
const packageForm = reactive({ label: '', yuan: 100, bonus_yuan: 0, enabled: true, sort_order: 10 })

const channelOpen = ref(false)
const channelEditing = ref<RechargeChannelRow | null>(null)
const channelForm = reactive({ code: '', display_name: '', merchant_id: '', secret: '', enabled: false })

async function fetchAll() {
  packages.value = (await api<Page<RechargePackageRow>>('/recharge/packages')).items
  channels.value = (await api<Page<RechargeChannelRow>>('/recharge/channels')).items
}
function load() {
  return run(fetchAll)
}

function editPackage(row?: RechargePackageRow) {
  packageEditing.value = row ?? null
  packageForm.label = row?.label ?? ''
  packageForm.yuan = (row?.amount_cent ?? 10000) / 100
  packageForm.bonus_yuan = (row?.bonus_cent ?? 0) / 100
  packageForm.enabled = row?.enabled ?? true
  packageForm.sort_order = row?.sort_order ?? 10
  packageOpen.value = true
}
function savePackage() {
  const body = {
    label: packageForm.label,
    amount_cent: toCent(packageForm.yuan),
    bonus_cent: toCent(packageForm.bonus_yuan),
    enabled: packageForm.enabled,
    sort_order: packageForm.sort_order,
  }
  return run(async () => {
    await api(packageEditing.value ? `/recharge/packages/${packageEditing.value.id}` : '/recharge/packages', {
      method: packageEditing.value ? 'PATCH' : 'POST',
      data: body,
    })
    packageOpen.value = false
    await fetchAll()
  }, '充值档位已保存')
}
function deletePackage(row: RechargePackageRow) {
  dialog.warning({
    title: '删除充值档位',
    content: `确认删除“${row.label}”？已入账的流水不受影响。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () =>
      run(async () => {
        await api(`/recharge/packages/${row.id}`, { method: 'DELETE' })
        await fetchAll()
      }, '档位已删除'),
  })
}

function editChannel(row?: RechargeChannelRow) {
  channelEditing.value = row ?? null
  channelForm.code = row?.code ?? ''
  channelForm.display_name = row?.display_name ?? ''
  channelForm.merchant_id = row?.merchant_id ?? ''
  // 密钥只写不读：编辑时永远从空开始，留空保存即清除。
  channelForm.secret = ''
  channelForm.enabled = row?.enabled ?? false
  channelOpen.value = true
}
function saveChannel() {
  const base = {
    display_name: channelForm.display_name,
    merchant_id: channelForm.merchant_id || null,
    secret: channelForm.secret,
    enabled: channelForm.enabled,
  }
  return run(async () => {
    if (channelEditing.value) {
      await api(`/recharge/channels/${channelEditing.value.id}`, { method: 'PATCH', data: base })
    } else {
      await api('/recharge/channels', { method: 'POST', data: { ...base, code: channelForm.code } })
    }
    channelOpen.value = false
    await fetchAll()
  }, '支付渠道已保存')
}
function deleteChannel(row: RechargeChannelRow) {
  dialog.warning({
    title: '删除支付渠道',
    content: `确认删除“${row.display_name}”？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () =>
      run(async () => {
        await api(`/recharge/channels/${row.id}`, { method: 'DELETE' })
        await fetchAll()
      }, '渠道已删除'),
  })
}

const packageColumns: DataTableColumns<RechargePackageRow> = [
  { title: '档位名称', key: 'label', minWidth: 180 },
  { title: '金额', key: 'amount_cent', width: 130, render: (row) => formatCent(row.amount_cent) },
  { title: '赠送', key: 'bonus_cent', width: 130, render: (row) => formatCent(row.bonus_cent) },
  { title: '到账', key: 'total', width: 140, render: (row) => formatCent(row.amount_cent + row.bonus_cent) },
  { title: '排序', key: 'sort_order', width: 90 },
  {
    title: '状态',
    key: 'enabled',
    width: 100,
    render: (row) => h(StatusPill, { on: row.enabled, onText: '已上架', offText: '已下架' }),
  },
  {
    title: '操作',
    key: 'actions',
    width: 150,
    render: (row) =>
      h(NSpace, { size: 4 }, () => [
        h(NButton, { size: 'small', quaternary: true, onClick: () => editPackage(row) }, () => '编辑'),
        h(NButton, { size: 'small', quaternary: true, type: 'error', onClick: () => deletePackage(row) }, () => '删除'),
      ]),
  },
]

const channelColumns: DataTableColumns<RechargeChannelRow> = [
  { title: '渠道代码', key: 'code', width: 140 },
  { title: '显示名称', key: 'display_name', minWidth: 160 },
  { title: '商户号', key: 'merchant_id', minWidth: 180, render: (row) => row.merchant_id ?? '—' },
  {
    title: '密钥',
    key: 'secret_configured',
    width: 130,
    render: (row) => h(StatusPill, { on: row.secret_configured, onText: '已配置', offText: '未配置' }),
  },
  {
    title: '状态',
    key: 'enabled',
    width: 110,
    render: (row) => h(StatusPill, { on: row.enabled, onText: '已开通', offText: '未开通' }),
  },
  { title: '更新时间', key: 'updated_at', width: 180, render: (row) => formatDate(row.updated_at) },
  {
    title: '操作',
    key: 'actions',
    width: 150,
    render: (row) =>
      h(NSpace, { size: 4 }, () => [
        h(NButton, { size: 'small', quaternary: true, onClick: () => editChannel(row) }, () => '编辑'),
        h(NButton, { size: 'small', quaternary: true, type: 'error', onClick: () => deleteChannel(row) }, () => '删除'),
      ]),
  },
]

const StatusPill = {
  props: { on: Boolean, onText: String, offText: String },
  setup(props: { on: boolean; onText?: string; offText?: string }) {
    return () =>
      h(
        'span',
        { class: props.on ? 'pill pill-on' : 'pill pill-off' },
        props.on ? props.onText ?? '是' : props.offText ?? '否'
      )
  },
}

onMounted(load)
</script>

<template>
  <PageHeader title="充值系统管理" description="档位、渠道与密钥在这里维护。在线支付尚未接入，档位不会自动产生入账。" />
  <NAlert type="info" style="margin-bottom: 22px">
    当前仅支持在「用户管理」里手动入账（渠道记 manual）。下面的配置是接入支付前的准备项，可先留空。
  </NAlert>

  <section class="section">
    <div class="page-heading">
      <div>
        <h1 style="font-size: 20px">充值档位</h1>
        <p>用户在门户充值页看到的金额档位；下架即从门户消失。</p>
      </div>
      <NButton type="primary" size="small" @click="editPackage()">新增档位</NButton>
    </div>
    <NEmpty v-if="!packages.length && !busy" description="尚未配置充值档位" />
    <NDataTable
      v-else
      :columns="packageColumns"
      :data="packages"
      :loading="busy"
      :row-key="(row) => row.id"
      :bordered="false"
      :scroll-x="920"
    />
  </section>

  <section class="section">
    <div class="page-heading">
      <div>
        <h1 style="font-size: 20px">支付渠道</h1>
        <p>渠道密钥只写不读，保存后不再回显，也不会出现在审计与日志里。</p>
      </div>
      <NButton type="primary" size="small" @click="editChannel()">新增渠道</NButton>
    </div>
    <NEmpty v-if="!channels.length && !busy" description="尚未开通支付渠道" />
    <NDataTable
      v-else
      :columns="channelColumns"
      :data="channels"
      :loading="busy"
      :row-key="(row) => row.id"
      :bordered="false"
      :scroll-x="1000"
    />
  </section>

  <NModal v-model:show="packageOpen" preset="card" :title="packageEditing ? '编辑充值档位' : '新增充值档位'" class="modal-form">
    <NFormItem label="档位名称"><NInput v-model:value="packageForm.label" :maxlength="100" /></NFormItem>
    <NFormItem label="金额（元）">
      <NInputNumber v-model:value="packageForm.yuan" :min="0.01" :max="1000000" :precision="2" style="width: 100%" />
    </NFormItem>
    <NFormItem label="赠送（元）">
      <NInputNumber v-model:value="packageForm.bonus_yuan" :min="0" :max="1000000" :precision="2" style="width: 100%" />
    </NFormItem>
    <NFormItem label="排序（小在前）">
      <NInputNumber v-model:value="packageForm.sort_order" :min="0" :max="9999" :precision="0" style="width: 160px" />
    </NFormItem>
    <NFormItem label="上架"><NSwitch v-model:value="packageForm.enabled" /></NFormItem>
    <div class="form-actions">
      <NButton @click="packageOpen = false">取消</NButton>
      <NButton type="primary" :loading="busy" :disabled="!packageForm.label.trim()" @click="savePackage">保存</NButton>
    </div>
  </NModal>

  <NModal v-model:show="channelOpen" preset="card" :title="channelEditing ? '编辑支付渠道' : '新增支付渠道'" class="modal-form">
    <NFormItem label="渠道代码">
      <NInput v-model:value="channelForm.code" :disabled="!!channelEditing" :maxlength="30" placeholder="小写字母、数字、-_，如 alipay" />
    </NFormItem>
    <NFormItem label="显示名称"><NInput v-model:value="channelForm.display_name" :maxlength="100" /></NFormItem>
    <NFormItem label="商户号"><NInput v-model:value="channelForm.merchant_id" :maxlength="200" /></NFormItem>
    <NFormItem :label="channelEditing ? '渠道密钥（留空即清除已存密钥）' : '渠道密钥'">
      <NInput
        v-model:value="channelForm.secret"
        type="password"
        show-password-on="click"
        :input-props="{ autocomplete: 'off' }"
        placeholder="保存后不再回显"
      />
    </NFormItem>
    <NFormItem label="开通"><NSwitch v-model:value="channelForm.enabled" /></NFormItem>
    <div class="form-actions">
      <NButton @click="channelOpen = false">取消</NButton>
      <NButton
        type="primary"
        :loading="busy"
        :disabled="!channelForm.code.trim() || !channelForm.display_name.trim()"
        @click="saveChannel"
      >
        保存
      </NButton>
    </div>
  </NModal>
</template>

<style scoped>
.pill {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 3px;
  font-size: 12px;
}
.pill-on {
  background: #e7f5ec;
  color: #137a43;
}
.pill-off {
  background: #f4f5f7;
  color: #767c82;
}
</style>
