<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NDataTable,
  NDivider,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NSwitch,
  useDialog,
  type DataTableColumns,
} from 'naive-ui'
import { api } from '../api/client'
import { formatCent, formatDate, toCent, useAction } from '../api/feedback'
import type {
  ChannelTypeSpec,
  CredentialFieldSpec,
  Page,
  RechargeChannelRow,
  RechargePackageRow,
  SelfCheckResult,
} from '../api/types'
import PageHeader from '../components/PageHeader.vue'

const { busy, run } = useAction()
const dialog = useDialog()
const packages = ref<RechargePackageRow[]>([])
const channels = ref<RechargeChannelRow[]>([])
// 表单字段全部来自服务端元数据，前端不复制一份厂商字段清单。
const specs = ref<ChannelTypeSpec[]>([])

const packageOpen = ref(false)
const packageEditing = ref<RechargePackageRow | null>(null)
const packageForm = reactive({ label: '', yuan: 100, bonus_yuan: 0, enabled: true, sort_order: 10 })

const createOpen = ref(false)
const createForm = reactive({ channel_type: 'alipay', code: '', display_name: '' })

const configOpen = ref(false)
const configTarget = ref<RechargeChannelRow | null>(null)
const configForm = reactive({ display_name: '' })
const drafts = reactive<Record<string, { value: string; dirty: boolean }>>({})

const verifyOpen = ref(false)
const verifyResult = ref<SelfCheckResult | null>(null)
const verifyTarget = ref('')

async function fetchAll() {
  const [packageData, channelData, specData] = await Promise.all([
    api<Page<RechargePackageRow>>('/recharge/packages'),
    api<Page<RechargeChannelRow>>('/recharge/channels'),
    api<{ types: ChannelTypeSpec[] }>('/recharge/credential-specs'),
  ])
  packages.value = packageData.items
  channels.value = channelData.items
  specs.value = specData.types
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

const typeLabels = computed(() => Object.fromEntries(specs.value.map((item) => [item.channel_type, item.display])))
const typeOptions = computed(() =>
  specs.value
    .filter((item) => item.channel_type !== 'custom')
    .map((item) => ({ label: item.display, value: item.channel_type }))
)
function specOf(channelType: string): ChannelTypeSpec | undefined {
  return specs.value.find((item) => item.channel_type === channelType)
}
// 下拉里露出"正式/沙箱"就够了，完整 URL 在字段提示里看。
function optionShort(option: string): string {
  if (option.includes('sandbox')) return '沙箱网关（openapi-sandbox.dl.alipaydev.com）'
  if (option.includes('openapi.alipay.com')) return '正式网关（openapi.alipay.com）'
  return option
}
// 浏览器密码管理器会按"文本框=用户名、password框=密码"的启发式往凭据表单里灌登录账号，
// autocomplete="off" 对密码框无效，所以密钥框用 new-password，并给每个框一个与账号无关的 name。
function inputName(key: string): string {
  return `kf-credential-${key.replace(/_/g, '-')}`
}

function openCreate() {
  createForm.channel_type = typeOptions.value[0]?.value ?? 'custom'
  createForm.code = ''
  createForm.display_name = ''
  createOpen.value = true
}
function saveChannel() {
  return run(async () => {
    await api('/recharge/channels', {
      method: 'POST',
      data: {
        code: createForm.code.trim(),
        display_name: createForm.display_name.trim(),
        channel_type: createForm.channel_type,
      },
    })
    createOpen.value = false
    await fetchAll()
  }, '渠道已创建，继续完善配置')
}

const configFields = computed<CredentialFieldSpec[]>(() => {
  const row = configTarget.value
  if (!row) return []
  const declared = specOf(row.channel_type)?.fields ?? []
  const known = new Set(declared.map((field) => field.key))
  // 迁移带过来的旧值也要出现在表单里，否则它永远藏在界面后面，既看不见也清不掉。
  const legacy = row.credentials
    .filter((item) => !known.has(item.key))
    .map<CredentialFieldSpec>((item) => ({
      key: item.key,
      label: item.label,
      help: '来自旧版单密钥字段，清空即删除。',
      secret: item.secret,
      required: false,
      editable: true,
      default: null,
      options: [],
      max_length: 8000,
      multiline: false,
    }))
  return [...declared, ...legacy]
})

function storedOf(key: string) {
  return configTarget.value?.credentials.find((item) => item.key === key)
}
function openConfig(row: RechargeChannelRow) {
  configTarget.value = row
  configForm.display_name = row.display_name
  for (const key of Object.keys(drafts)) delete drafts[key]
  for (const field of specOf(row.channel_type)?.fields ?? []) {
    const stored = row.credentials.find((item) => item.key === field.key)
    // 有默认值的项（如网关地址）先选中默认，别让人以为还要自己动手填一遍。
    drafts[field.key] = { value: stored?.value ?? field.default ?? '', dirty: false }
  }
  for (const item of row.credentials) {
    if (!(item.key in drafts)) drafts[item.key] = { value: item.value ?? '', dirty: false }
  }
  configOpen.value = true
}
function touch(key: string, value: string) {
  const draft = drafts[key]
  if (!draft) return
  draft.value = value
  draft.dirty = true
}
function credentialPayload(): Record<string, string | null> {
  const payload: Record<string, string | null> = {}
  for (const field of configFields.value) {
    const draft = drafts[field.key]
    // 没动过的字段绝不进 body：旧版总是把空密钥发出去，改个显示名就顺手把密钥清了。
    if (!draft?.dirty) continue
    payload[field.key] = draft.value.trim() || null
  }
  return payload
}
function saveConfig() {
  const row = configTarget.value
  if (!row) return Promise.resolve()
  return run(async () => {
    await api(`/recharge/channels/${row.id}`, {
      method: 'PATCH',
      data: { display_name: configForm.display_name.trim(), credentials: credentialPayload() },
    })
    configOpen.value = false
    await fetchAll()
  }, '渠道配置已保存')
}

function hintOf(field: CredentialFieldSpec): string | undefined {
  const stored = storedOf(field.key)
  const parts: string[] = []
  if (field.required && !stored) parts.push('必填')
  if (field.help) parts.push(field.help)
  if (stored) {
    parts.push(
      field.secret
        ? `已配置 · 指纹 ${stored.fingerprint} · 版本 ${stored.key_version} · ${formatDate(stored.set_at)}`
        : `版本 ${stored.key_version} · ${formatDate(stored.set_at)}`
    )
  } else if (field.secret) {
    parts.push('保存后不再回显；粘贴新值即轮换，清空即删除。')
  }
  return parts.length ? parts.join(' · ') : undefined
}
// 必填的厂商身份/密钥放前面，回调地址与可选证书放后面，中间一条分隔线，
// 这样第一屏只看到真正必须填的那几项。分隔线取"最后一个必填项之后"：
// 必填项在规格里并不连续（网关地址可选、应用私钥必填），按第一个可选项切会把必填项漏到下面。
const optionalStart = computed(() => {
  let lastRequired = -1
  configFields.value.forEach((field, index) => {
    if (field.required) lastRequired = index
  })
  return lastRequired >= 0 && lastRequired + 1 < configFields.value.length ? lastRequired + 1 : -1
})
const stillMissing = computed(() => {
  const row = configTarget.value
  if (!row) return ''
  const reasons: string[] = []
  if (row.configuration.missing.length) reasons.push(`缺 ${row.configuration.missing.join('、')}`)
  if (row.configuration.problem) reasons.push(row.configuration.problem)
  return reasons.join('；')
})

function verify(row: RechargeChannelRow) {
  verifyTarget.value = row.display_name
  return run(async () => {
    verifyResult.value = await api<SelfCheckResult>(`/recharge/channels/${row.id}/verify`, { method: 'POST' })
    verifyOpen.value = true
    await fetchAll()
  })
}
function toggleEnabled(row: RechargeChannelRow) {
  return run(async () => {
    await api(`/recharge/channels/${row.id}`, { method: 'PATCH', data: { enabled: !row.enabled } })
    await fetchAll()
  }, row.enabled ? '渠道已停用' : '渠道已启用')
}
function deleteChannel(row: RechargeChannelRow) {
  dialog.warning({
    title: '删除支付渠道',
    content: `确认删除“${row.display_name}”？已入账的流水不受影响，但渠道凭据会一并删除。`,
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

function missingReasons(row: RechargeChannelRow): string {
  const reasons: string[] = []
  if (row.configuration.missing.length) reasons.push(`缺 ${row.configuration.missing.join('、')}`)
  if (row.configuration.problem) reasons.push(row.configuration.problem)
  // 缺项与"两种模式只配了一半"可以同时成立，只报一条会让人修完一样再撞另一样。
  return reasons.join('；')
}

function configurationPill(row: RechargeChannelRow): { on: boolean; text: string } {
  if (!row.configuration.payable) return { on: false, text: '不适用' }
  if (!row.configuration.complete) return { on: false, text: row.enabled ? '配置不完整' : '待配置' }
  if (!row.verification.checked) return { on: false, text: '未自检' }
  return row.verification.passed ? { on: true, text: '自检通过' } : { on: false, text: '自检未通过' }
}

function configurationNote(row: RechargeChannelRow): string {
  if (!row.configuration.payable) return '不支持公众号在线下单'
  const reasons = missingReasons(row)
  if (reasons) return reasons
  if (!row.verification.checked) return `${row.credentials.length} 项配置就绪，自检未做过，厂商侧还不保证可用`
  if (row.verification.passed) return `${row.credentials.length} 项配置 · 自检于 ${formatDate(row.verification.at)}`
  return row.verification.detail ?? '自检未通过'
}

const channelColumns: DataTableColumns<RechargeChannelRow> = [
  { title: '渠道代码', key: 'code', width: 140 },
  { title: '显示名称', key: 'display_name', minWidth: 150 },
  {
    title: '类型',
    key: 'channel_type',
    width: 100,
    render: (row) => typeLabels.value[row.channel_type] ?? row.channel_type,
  },
  {
    title: '配置',
    key: 'configuration',
    minWidth: 240,
    render: (row) => {
      const pill = configurationPill(row)
      return h('div', { class: 'cell-stacked' }, [
        h(StatusPill, { on: pill.on, onText: pill.text, offText: pill.text }),
        h('span', { class: 'muted' }, configurationNote(row)),
      ])
    },
  },
  {
    title: '状态',
    key: 'enabled',
    width: 100,
    render: (row) => h(StatusPill, { on: row.enabled, onText: '已启用', offText: '已停用' }),
  },
  { title: '更新时间', key: 'updated_at', width: 170, render: (row) => formatDate(row.updated_at) },
  {
    title: '操作',
    key: 'actions',
    width: 260,
    render: (row) =>
      h(NSpace, { size: 4 }, () => [
        h(NButton, { size: 'small', onClick: () => openConfig(row) }, () => '完善配置'),
        h(
          NButton,
          {
            size: 'small',
            quaternary: true,
            disabled: !row.configuration.payable || busy.value,
            onClick: () => verify(row),
          },
          () => '自检'
        ),
        h(
          NButton,
          { size: 'small', quaternary: true, onClick: () => toggleEnabled(row) },
          () => (row.enabled ? '停用' : '启用')
        ),
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
  <PageHeader title="充值系统管理" description="档位与支付渠道凭据在这里维护；密钥只写不读，列表只显示指纹。" />
  <NAlert type="info" style="margin-bottom: 22px">
    渠道按厂商收全凭据并可做连通性自检；在线下单与回调入账接入前，入账仍走「用户管理」手动入账（渠道记 manual）。
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
        <p>先建渠道选类型，再点「完善配置」按厂商填凭据；密钥保存后不再回显，也不会出现在审计与日志里。</p>
      </div>
      <NButton type="primary" size="small" @click="openCreate">新增渠道</NButton>
    </div>
    <NEmpty v-if="!channels.length && !busy" description="尚未开通支付渠道" />
    <NDataTable
      v-else
      :columns="channelColumns"
      :data="channels"
      :loading="busy"
      :row-key="(row) => row.id"
      :bordered="false"
      :scroll-x="1180"
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

  <NModal v-model:show="createOpen" preset="card" title="新增支付渠道" class="modal-form">
    <NFormItem label="渠道类型">
      <NSelect v-model:value="createForm.channel_type" :options="typeOptions" />
    </NFormItem>
    <NFormItem label="渠道代码">
      <NInput v-model:value="createForm.code" :maxlength="30" placeholder="小写字母、数字、-_，如 alipay-web" />
    </NFormItem>
    <NFormItem label="显示名称"><NInput v-model:value="createForm.display_name" :maxlength="100" /></NFormItem>
    <p class="muted">代码建好后不可修改：入账流水按它记录渠道。凭据请在列表里点「完善配置」填写。</p>
    <div class="form-actions">
      <NButton @click="createOpen = false">取消</NButton>
      <NButton
        type="primary"
        :loading="busy"
        :disabled="!createForm.code.trim() || !createForm.display_name.trim()"
        @click="saveChannel"
      >
        下一步：完善配置
      </NButton>
    </div>
  </NModal>

  <NModal
    v-model:show="configOpen"
    preset="card"
    :title="`完善配置 · ${configTarget?.display_name ?? ''}`"
    :segmented="{ content: true, footer: 'soft' }"
    class="channel-form"
  >
    <template #header-extra>
      <span class="muted channel-subtitle">
        {{ configTarget?.code }} · {{ typeLabels[configTarget?.channel_type ?? ''] ?? configTarget?.channel_type }}
      </span>
    </template>
    <NForm label-placement="left" label-align="left" label-width="150" autocomplete="off">
      <NFormItem label="显示名称">
        <NInput v-model:value="configForm.display_name" :maxlength="100" :input-props="{ autocomplete: 'off', name: 'kf-channel-name' }" />
      </NFormItem>
      <template v-for="(field, index) in configFields" :key="field.key">
        <NDivider v-if="index === optionalStart" class="form-divider" />
        <NFormItem :label="field.label" :required="field.required" :feedback="hintOf(field)">
          <NSelect
            v-if="field.options.length"
            :value="drafts[field.key]?.value ?? null"
            :options="field.options.map((option) => ({ label: optionShort(option), value: option }))"
            :disabled="!field.editable"
            @update:value="(value: string) => touch(field.key, value)"
          />
          <NInput
            v-else-if="field.multiline"
            :value="drafts[field.key]?.value ?? ''"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 10 }"
            :input-props="{ autocomplete: 'off', name: inputName(field.key), class: 'pem-input' }"
            :maxlength="field.max_length"
            :disabled="!field.editable"
            :placeholder="storedOf(field.key) ? '留空即保持不变，粘贴新值即轮换' : '粘贴厂商给的内容，裸 base64 或 PEM 都行'"
            @update:value="(value: string) => touch(field.key, value)"
          />
          <NInput
            v-else
            :value="drafts[field.key]?.value ?? ''"
            :type="field.secret ? 'password' : 'text'"
            :show-password-on="field.secret ? 'click' : undefined"
            :input-props="{ autocomplete: field.secret ? 'new-password' : 'off', name: inputName(field.key) }"
            :maxlength="field.max_length"
            :disabled="!field.editable"
            :placeholder="storedOf(field.key) ? '留空即保持不变' : ''"
            @update:value="(value: string) => touch(field.key, value)"
          />
        </NFormItem>
      </template>
    </NForm>
    <p v-if="stillMissing" class="muted form-summary">补齐后才能在列表里启用：{{ stillMissing }}</p>
    <template #footer>
      <div class="form-actions form-actions--flush">
        <NButton @click="configOpen = false">取消</NButton>
        <NButton type="primary" :loading="busy" @click="saveConfig">保存配置</NButton>
      </div>
    </template>
  </NModal>

  <NModal v-model:show="verifyOpen" preset="card" :title="`连通性自检 · ${verifyTarget}`" class="modal-form">
    <NAlert :type="verifyResult?.passed ? 'success' : 'error'" style="margin-bottom: 14px">
      {{ verifyResult?.passed ? '自检通过' : '自检未通过' }} ·
      {{ verifyResult?.mode === 'live' ? '已实际调用厂商接口' : '仅本地校验，未联调厂商接口' }}
    </NAlert>
    <div v-for="item in verifyResult?.checks ?? []" :key="item.name" class="check-row">
      <StatusPill :on="item.ok" on-text="通过" off-text="未通过" />
      <div class="cell-stacked">
        <strong>{{ item.name }}</strong>
        <span class="muted">{{ item.detail || '—' }}</span>
      </div>
    </div>
    <div class="form-actions">
      <NButton @click="verifyOpen = false">关闭</NButton>
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
.channel-subtitle {
  font-size: 13px;
  margin-left: 12px;
}
.form-divider {
  margin: 6px 0 18px;
}
.form-summary {
  margin: 4px 0 0;
  font-size: 13px;
}
.form-actions--flush {
  margin-top: 0;
}
.cell-stacked {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.check-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid #efeff5;
}
</style>
