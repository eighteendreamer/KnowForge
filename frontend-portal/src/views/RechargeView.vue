<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NAlert, NButton, NDataTable, NTag, type DataTableColumns } from 'naive-ui'
import { useRouter } from 'vue-router'
import { api, formatCent } from '../api/client'
import { formatDate, useAction } from '../api/feedback'
import type { PaymentOrderData, TransactionRow, WalletData } from '../api/types'
import PageHeader from '../components/PageHeader.vue'

const STATUS_LABEL: Record<PaymentOrderData['status'], string> = {
  created: '已下单',
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

const { busy, run } = useAction()
const router = useRouter()
const wallet = ref<WalletData | null>(null)
const orders = ref<PaymentOrderData[]>([])

const packages = computed(() => wallet.value?.packages ?? [])
const payable = computed(() => (wallet.value?.channels ?? []).filter((row) => row.orderable))
const paidTotal = computed(() =>
  orders.value.filter((row) => row.status === 'paid').reduce((sum, row) => sum + row.credited_cent, 0)
)

async function load() {
  const [data, recent] = await Promise.all([
    api<WalletData>('/wallet'),
    api<{ items: PaymentOrderData[] }>('/orders'),
  ])
  wallet.value = data
  orders.value = recent.items
}

function goCheckout(packageId: number) {
  router.push({ name: 'recharge-checkout', query: { package: String(packageId) } })
}

// 继续付的是同一张单、同一张码，不是重新下一单：另开一单会让上一张码变成废码。
function resumeCheckout(reference: string) {
  router.push({ name: 'recharge-checkout', query: { order: reference } })
}

onMounted(() => void run(load))

const columns: DataTableColumns<PaymentOrderData> = [
  { title: '时间', key: 'created_at', width: 180, render: (row) => formatDate(row.created_at) },
  { title: '商户单号', key: 'out_trade_no', minWidth: 200, render: (row) => h('code', null, row.out_trade_no) },
  { title: '实付', key: 'payable_cent', width: 120, render: (row) => formatCent(row.payable_cent) },
  { title: '到账', key: 'credited_cent', width: 120, render: (row) => formatCent(row.credited_cent) },
  { title: '支付方式', key: 'channel_name', width: 130 },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) =>
      h(NTag, { size: 'small', bordered: false, type: STATUS_TAG[row.status] }, () => STATUS_LABEL[row.status]),
  },
  {
    title: '',
    key: 'actions',
    width: 110,
    render: (row) =>
      row.status === 'pending' || row.status === 'created'
        ? h(NButton, { size: 'small', onClick: () => resumeCheckout(row.out_trade_no) }, () => '继续支付')
        : h('span', { class: 'muted' }, '—'),
  },
]

const ledgerColumns: DataTableColumns<TransactionRow> = [
  { title: '时间', key: 'created_at', width: 190, render: (row) => formatDate(row.created_at) },
  { title: '入账金额', key: 'amount_cent', width: 140, render: (row) => formatCent(row.amount_cent) },
  { title: '来源', key: 'channel', width: 120 },
  { title: '备注', key: 'note', minWidth: 220, render: (row) => row.note ?? '—' },
]
</script>

<template>
  <PageHeader title="充值" description="选档位进入结算页，扫码付款后由系统按厂商回执自动入账。">
    <NButton size="small" :loading="busy" @click="run(load)">刷新</NButton>
  </PageHeader>

  <template v-if="wallet">
    <div class="metric-strip">
      <div class="metric">
        <strong>{{ formatCent(wallet.balance_cent) }}</strong>
        <span>当前余额</span>
      </div>
      <div class="metric">
        <strong>{{ formatCent(paidTotal) }}</strong>
        <span>最近订单累计到账</span>
      </div>
      <div class="metric">
        <strong>{{ payable.length }}</strong>
        <span>可用支付方式</span>
      </div>
    </div>

    <section class="section">
      <h2>选择充值金额</h2>
      <NAlert v-if="!packages.length || !payable.length" type="info" style="margin-top: 14px">
        支付通道尚未开通，暂时无法在线下单。需要额度请联系平台在后台为你的账号入账。
      </NAlert>
      <div v-else class="tile-grid">
        <button
          v-for="row in packages"
          :key="row.id"
          type="button"
          class="tile tile--amount"
          @click="goCheckout(row.id)"
        >
          <strong>{{ formatCent(row.amount_cent) }}</strong>
          <span v-if="row.bonus_cent" class="tile-tag">赠 {{ formatCent(row.bonus_cent) }}</span>
          <span class="tile-note">到账 {{ formatCent(row.amount_cent + row.bonus_cent) }}</span>
        </button>
      </div>
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
</template>
