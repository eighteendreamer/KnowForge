<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue'
import { NAlert, NButton, NDataTable, NModal, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import QRCode from 'qrcode'
import { api, errorMessage, formatCent } from '../api/client'
import { formatDate, useAction } from '../api/feedback'
import type { PaymentOrderData, OrderSyncData, TransactionRow, WalletData } from '../api/types'
import PageHeader from '../components/PageHeader.vue'

const SYNC_INTERVAL_MS = 10_000
const STATUS_LABEL: Record<PaymentOrderData['status'], string> = {
  created: '待确认',
  pending: '待支付',
  paid: '已到账',
  expired: '已超时',
  failed: '下单失败',
}
const STATUS_TAG: Record<PaymentOrderData['status'], 'default' | 'info' | 'success' | 'warning'> = {
  created: 'info',
  pending: 'warning',
  paid: 'success',
  expired: 'default',
  failed: 'default',
}
// 渠道类型是内部术语，给用户看的是"这笔钱怎么付"。
const PAY_STYLE: Record<string, string> = {
  alipay: '网页跳转付款',
  wechat: '扫码付款',
  stripe: '收银台付款',
}

const { busy, run } = useAction()
const message = useMessage()
const wallet = ref<WalletData | null>(null)
const orders = ref<PaymentOrderData[]>([])
const packageId = ref<number | null>(null)
const channelCode = ref('')
const active = ref<PaymentOrderData | null>(null)
const qrImage = ref('')
const payOpen = ref(false)
const syncing = ref(false)
const syncNote = ref('')
const now = ref(Date.now())
let ticker: number | undefined
let lastSyncAt = 0

const packages = computed(() => wallet.value?.packages ?? [])
const payable = computed(() => (wallet.value?.channels ?? []).filter((row) => row.orderable))
const chosen = computed(() => packages.value.find((row) => row.id === packageId.value) ?? null)
const canPay = computed(() => Boolean(chosen.value && channelCode.value))
const remainingMs = computed(() =>
  active.value ? new Date(active.value.expires_at).getTime() - now.value : 0
)
const remaining = computed(() => {
  const total = Math.max(0, Math.floor(remainingMs.value / 1000))
  return `${Math.floor(total / 60)} 分 ${String(total % 60).padStart(2, '0')} 秒`
})

async function loadOrders() {
  const recent = await api<{ items: PaymentOrderData[] }>('/orders')
  orders.value = recent.items
}

async function load() {
  const data = await api<WalletData>('/wallet')
  wallet.value = data
  if (!packages.value.some((row) => row.id === packageId.value)) {
    packageId.value = packages.value[0]?.id ?? null
  }
  if (!payable.value.some((row) => row.code === channelCode.value)) {
    channelCode.value = payable.value[0]?.code ?? ''
  }
  await loadOrders()
}

function stopPolling() {
  if (ticker !== undefined) window.clearInterval(ticker)
  ticker = undefined
}

function startPolling() {
  stopPolling()
  now.value = Date.now()
  lastSyncAt = Date.now()
  ticker = window.setInterval(() => {
    now.value = Date.now()
    const order = active.value
    if (!order || order.status !== 'pending' || remainingMs.value <= 0) return stopPolling()
    if (Date.now() - lastSyncAt >= SYNC_INTERVAL_MS) void sync(true)
  }, 1000)
}

function applyOrder(next: PaymentOrderData) {
  active.value = next
  orders.value = orders.value.map((row) => (row.out_trade_no === next.out_trade_no ? next : row))
}

async function openOrder(order: PaymentOrderData, note = '') {
  active.value = order
  payOpen.value = true
  syncNote.value = note
  qrImage.value = order.code_url ? await QRCode.toDataURL(order.code_url, { margin: 1, width: 224 }) : ''
  startPolling()
}

async function pay() {
  if (!chosen.value || !channelCode.value) return
  const target = chosen.value
  await run(async () => {
    const order = await api<PaymentOrderData>('/orders', {
      method: 'POST',
      data: { channel_code: channelCode.value, package_id: target.id },
    })
    await openOrder(order, '订单已创建，等待你完成支付')
    // 新单要立刻出现在「最近订单」里：页面上写着"可以回来继续"，刷不出来就是句空话。
    await loadOrders()
  })
}

async function sync(auto: boolean) {
  const order = active.value
  if (!order) return
  if (!auto) syncing.value = true
  lastSyncAt = Date.now()
  try {
    const result = await api<OrderSyncData>(`/orders/${order.out_trade_no}/sync`, { method: 'POST' })
    applyOrder(result)
    syncNote.value = result.status === 'paid' ? '支付已确认，额度已到账' : result.sync.detail || '还没有收到支付结果'
    if (result.status === 'paid') {
      stopPolling()
      message.success('充值已到账')
      await load()
    }
  } catch (error) {
    // 厂商临时不可用只更新这一行提示：轮询下一轮还会再试，不该打断用户的支付动作。
    syncNote.value = errorMessage(error)
  } finally {
    syncing.value = false
  }
}

function closePay() {
  stopPolling()
  payOpen.value = false
}

onBeforeUnmount(stopPolling)

function openProviderPage() {
  const url = active.value?.redirect_url
  if (url) window.open(url, '_blank', 'noopener')
}

const columns: DataTableColumns<PaymentOrderData> = [
  { title: '时间', key: 'created_at', width: 180, render: (row) => formatDate(row.created_at) },
  { title: '商户单号', key: 'out_trade_no', minWidth: 200, render: (row) => h('code', null, row.out_trade_no) },
  { title: '金额', key: 'amount_cent', width: 120, render: (row) => formatCent(row.amount_cent) },
  { title: '支付方式', key: 'channel_name', width: 130 },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) =>
      h(NTag, { size: 'small', bordered: false, type: STATUS_TAG[row.status] }, () => STATUS_LABEL[row.status]),
  },
  {
    title: '',
    key: 'actions',
    width: 130,
    render: (row) =>
      row.status === 'pending' || row.status === 'created'
        ? h(NButton, { size: 'small', onClick: () => void openOrder(row, '') }, () => '继续支付')
        : h('span', { class: 'muted' }, '—'),
  },
]

const ledgerColumns: DataTableColumns<TransactionRow> = [
  { title: '时间', key: 'created_at', width: 190, render: (row) => formatDate(row.created_at) },
  { title: '入账金额', key: 'amount_cent', width: 140, render: (row) => formatCent(row.amount_cent) },
  { title: '来源', key: 'channel', width: 120 },
  { title: '备注', key: 'note', minWidth: 220, render: (row) => row.note ?? '—' },
]

const totalPaidOrders = computed(() => orders.value.filter((row) => row.status === 'paid').length)

onMounted(() => void run(load))
</script>

<template>
  <PageHeader title="充值" description="选金额、选支付方式，付款完成后由系统按厂商回执自动入账。">
    <NButton size="small" :loading="busy" @click="run(load)">刷新</NButton>
  </PageHeader>

  <template v-if="wallet">
    <div class="metric-strip">
      <div class="metric">
        <strong>{{ formatCent(wallet.balance_cent) }}</strong>
        <span>当前余额</span>
      </div>
      <div class="metric">
        <strong>{{ totalPaidOrders }}</strong>
        <span>最近已到账订单</span>
      </div>
      <div class="metric">
        <strong>{{ payable.length }}</strong>
        <span>可用支付方式</span>
      </div>
    </div>

    <section class="section">
      <h2>在线充值</h2>
      <NAlert v-if="!packages.length || !payable.length" type="info" style="margin-top: 14px">
        支付通道尚未开通，暂时无法在线下单。需要额度请联系平台在后台为你的账号入账。
      </NAlert>
      <template v-else>
        <div class="checkout">
          <div class="checkout-group">
            <h3>选择金额</h3>
            <button
              v-for="row in packages"
              :key="row.id"
              type="button"
              class="option-row"
              :class="{ 'option-row--active': row.id === packageId }"
              @click="packageId = row.id"
            >
              <span class="option-main">{{ formatCent(row.amount_cent) }}</span>
              <span v-if="row.bonus_cent" class="option-tag">赠 {{ formatCent(row.bonus_cent) }}</span>
              <span class="option-tail">到账 {{ formatCent(row.amount_cent + row.bonus_cent) }}</span>
            </button>
          </div>
          <div class="checkout-group">
            <h3>支付方式</h3>
            <button
              v-for="row in payable"
              :key="row.code"
              type="button"
              class="option-row"
              :class="{ 'option-row--active': row.code === channelCode }"
              @click="channelCode = row.code"
            >
              <span class="option-main">{{ row.display_name }}</span>
              <span class="option-tail">{{ PAY_STYLE[row.channel_type] ?? row.channel_type }}</span>
            </button>
          </div>
        </div>
        <div class="checkout-actions">
          <span class="payable">应付 {{ chosen ? formatCent(chosen.amount_cent) : '—' }}</span>
          <NButton type="primary" :disabled="!canPay" :loading="busy" @click="pay">立即支付</NButton>
          <span class="muted">下单后未付款的订单保留 30 分钟，可在下方「最近订单」里回来继续。</span>
        </div>
      </template>
    </section>

    <section class="section">
      <h2>最近订单</h2>
      <p v-if="!orders.length" class="muted section-empty">还没有下过单。</p>
      <NDataTable
        v-else
        :columns="columns"
        :data="orders"
        :row-key="(row) => row.out_trade_no"
        :bordered="false"
        size="small"
      />
    </section>

    <section class="section">
      <h2>入账流水</h2>
      <p v-if="!wallet.transactions.length" class="muted section-empty">尚无入账记录。</p>
      <NDataTable
        v-else
        :columns="ledgerColumns"
        :data="wallet.transactions"
        :row-key="(row) => row.id"
        :bordered="false"
        :pagination="{ pageSize: 20 }"
      />
    </section>
  </template>

  <NModal v-model:show="payOpen" preset="card" title="支付订单" class="pay-dialog">
    <template v-if="active">
      <div class="pay-grid">
        <div v-if="active.code_url" class="pay-qr">
          <img v-if="qrImage" :src="qrImage" alt="支付二维码" width="224" height="224">
          <p class="muted">用微信扫上面的二维码完成付款。</p>
        </div>
        <div v-else-if="active.redirect_url" class="pay-redirect">
          <NButton type="primary" @click="openProviderPage">前往{{ active.channel_name }}</NButton>
          <p class="muted">会在新标签页打开厂商收银台，付款后回到本页即可，别关掉这个窗口。</p>
        </div>
        <dl class="pay-facts">
          <dt>商户单号</dt>
          <dd><code>{{ active.out_trade_no }}</code></dd>
          <dt>应付金额</dt>
          <dd>{{ formatCent(active.amount_cent) }}</dd>
          <dt>支付后到账</dt>
          <dd>{{ formatCent(active.credited_cent || active.amount_cent + active.bonus_cent) }}</dd>          <dt>支付倒计时</dt>
          <dd>{{ remainingMs > 0 ? remaining : '订单已超时关闭' }}</dd>
        </dl>
      </div>
      <p class="pay-note" :class="{ 'pay-note--paid': active.status === 'paid' }">
        {{ syncNote || '正在等待厂商回执，页面会自动确认结果。' }}
      </p>
    </template>
    <template #footer>
      <div class="row row-actions">
        <NButton :loading="syncing" :disabled="active?.status === 'paid'" @click="sync(false)">我已完成支付</NButton>
        <span class="muted">没有收到结果时点这个，系统会直接向厂商查单确认。</span>
        <NButton style="margin-left: auto" @click="closePay">{{ active?.status === 'paid' ? '完成' : '稍后支付' }}</NButton>
      </div>
    </template>
  </NModal>
</template>
