<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NAlert, NButton, NInput, NTag, useMessage } from 'naive-ui'
import QRCode from 'qrcode'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage, formatCent } from '../api/client'
import { useAction } from '../api/feedback'
import type { OrderSyncData, PaymentOrderData, PromoQuote, WalletData } from '../api/types'
import PageHeader from '../components/PageHeader.vue'

const SYNC_INTERVAL_MS = 10_000
// 渠道类型是内部术语，给用户看的是"这笔钱怎么付"。
const PAY_STYLE: Record<string, string> = {
  alipay: '用支付宝扫码',
  wechat: '用微信扫码',
  stripe: '跳转收银台',
}

const route = useRoute()
const router = useRouter()
const { busy, run } = useAction()
const message = useMessage()

const wallet = ref<WalletData | null>(null)
const packageId = ref(Number(route.query.package ?? 0))
const channelCode = ref(String(route.query.channel ?? ''))
const promoInput = ref('')
const quote = ref<PromoQuote | null>(null)
const promoBusy = ref(false)
const order = ref<PaymentOrderData | null>(null)
const qrImage = ref('')
const syncing = ref(false)
const statusNote = ref('')
const now = ref(Date.now())
let ticker: number | undefined
let lastSyncAt = 0

const packages = computed(() => wallet.value?.packages ?? [])
const payableChannels = computed(() => (wallet.value?.channels ?? []).filter((row) => row.orderable))
const chosen = computed(() => packages.value.find((row) => row.id === packageId.value) ?? null)
const channel = computed(() => payableChannels.value.find((row) => row.code === channelCode.value) ?? null)
const paying = computed(() => order.value !== null)
const paid = computed(() => order.value?.status === 'paid')
const remainingMs = computed(() =>
  order.value ? new Date(order.value.expires_at).getTime() - now.value : 0
)
const countdown = computed(() => {
  const total = Math.max(0, Math.floor(remainingMs.value / 1000))
  return `${Math.floor(total / 60)} 分 ${String(total % 60).padStart(2, '0')} 秒`
})
const priced = computed(() => quote.value)
const payableCent = computed(() =>
  order.value ? order.value.payable_cent : (priced.value?.payable_cent ?? chosen.value?.amount_cent ?? 0)
)
const creditedCent = computed(() =>
  order.value
    ? order.value.amount_cent + order.value.bonus_cent
    : priced.value
      ? priced.value.credited_cent
      : (chosen.value?.amount_cent ?? 0) + (chosen.value?.bonus_cent ?? 0)
)
const discountCent = computed(() =>
  order.value ? order.value.discount_cent : (priced.value?.discount_cent ?? 0)
)
const listPrice = computed(() =>
  order.value ? order.value.amount_cent : (chosen.value?.amount_cent ?? 0)
)
const canPay = computed(() => Boolean(chosen.value && channel.value))

async function load() {
  wallet.value = await api<WalletData>('/wallet')
  const resume = String(route.query.order ?? '')
  if (resume) {
    // 从「最近订单」进来是接着付同一张单：直接回到扫码步骤，不再下一单。
    const existing = await api<PaymentOrderData>(`/orders/${resume}`)
    if (existing.package_id) packageId.value = existing.package_id
    channelCode.value = existing.channel_code
    await showOrder(existing)
    return
  }
  if (!packages.value.some((row) => row.id === packageId.value)) {
    // 直接输入或收藏一个已下架的档位：回列表去选，而不是给一个空白结算页。
    await router.replace({ name: 'recharge' })
    return
  }
  if (!payableChannels.value.some((row) => row.code === channelCode.value)) {
    channelCode.value = payableChannels.value[0]?.code ?? ''
  }
}

async function showOrder(created: PaymentOrderData) {
  order.value = created
  statusNote.value = created.status === 'pending' ? '订单已生成，请扫码完成付款' : ''
  qrImage.value = created.code_url
    ? await QRCode.toDataURL(created.code_url, { margin: 1, width: 224 })
    : ''
  if (created.status === 'pending') startPolling()
  else stopPolling()
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
    if (!order.value || order.value.status !== 'pending' || remainingMs.value <= 0) return stopPolling()
    if (Date.now() - lastSyncAt >= SYNC_INTERVAL_MS) void sync(true)
  }, 1000)
}

async function applyPromo() {
  if (!chosen.value) return
  promoBusy.value = true
  try {
    quote.value = await api<PromoQuote>('/promo/quote', {
      method: 'POST',
      data: { package_id: chosen.value.id, promo_code: promoInput.value.trim() },
    })
    if (!quote.value.applied && promoInput.value.trim()) message.warning(quote.value.reason || '促销码不可用')
  } catch (error) {
    quote.value = null
    message.error(errorMessage(error))
  } finally {
    promoBusy.value = false
  }
}

function clearPromo() {
  promoInput.value = ''
  quote.value = null
}

function pickChannel(code: string) {
  channelCode.value = code
  // 换了渠道就等于换一单去付：旧单还挂在厂商那边，扫码时不能同时认两张码。
  if (order.value && order.value.status !== 'paid') backToForm('已切换支付方式，请重新下单')
}

function backToForm(note = '') {
  stopPolling()
  order.value = null
  qrImage.value = ''
  statusNote.value = note
}

async function pay() {
  if (!chosen.value || !channel.value) return
  const target = chosen.value
  await run(async () => {
    const created = await api<PaymentOrderData>('/orders', {
      method: 'POST',
      data: {
        channel_code: channel.value!.code,
        package_id: target.id,
        promo_code: quote.value?.applied ? promoInput.value.trim() : '',
      },
    })
    order.value = created
    statusNote.value = '订单已生成，请扫码完成付款'
    qrImage.value = created.code_url
      ? await QRCode.toDataURL(created.code_url, { margin: 1, width: 224 })
      : ''
    startPolling()
  })
}

async function sync(auto: boolean) {
  const current = order.value
  if (!current) return
  if (!auto) syncing.value = true
  lastSyncAt = Date.now()
  try {
    const result = await api<OrderSyncData>(`/orders/${current.out_trade_no}/sync`, { method: 'POST' })
    order.value = result
    statusNote.value =
      result.status === 'paid' ? '支付已确认，额度已到账' : result.sync.detail || '还没有收到支付结果'
    if (result.status === 'paid') {
      stopPolling()
      message.success('充值已到账')
    }
  } catch (error) {
    statusNote.value = errorMessage(error)
  } finally {
    syncing.value = false
  }
}

onBeforeUnmount(stopPolling)
onMounted(() => void run(load))

function done() {
  router.push({ name: 'recharge' })
}
</script>

<template>
  <PageHeader title="确认订单" description="核对金额、用促销码、选支付方式，然后扫码付款。">
    <NButton size="small" quaternary @click="done">返回充值页</NButton>
  </PageHeader>

  <template v-if="wallet && (chosen || order)">
    <div class="checkout-grid">
      <div class="checkout-main">
        <section class="block">
          <h2>支付订单</h2>
          <div class="order-line">
            <div>
              <strong>{{ chosen?.label ?? '已生成的订单' }}</strong>
              <span class="muted">充值 {{ formatCent(listPrice) }}</span>
            </div>
            <div class="order-amount">
              <strong>{{ formatCent(payableCent) }}</strong>
              <span v-if="discountCent" class="strike muted">{{ formatCent(listPrice) }}</span>
            </div>
          </div>
          <p class="muted block-note">
            本档赠送 {{ formatCent(order ? order.bonus_cent : (chosen?.bonus_cent ?? 0)) }}，实付
            {{ formatCent(payableCent) }}，到账 {{ formatCent(creditedCent) }}。
          </p>
        </section>

        <section class="block">
          <h2>促销码</h2>
          <div class="promo-row">
            <NInput
              v-model:value="promoInput"
              :maxlength="32"
              placeholder="输入促销码，没有可不填"
              :disabled="paying"
              @update:value="quote = null"
            />
            <NButton :loading="promoBusy" :disabled="paying || !promoInput.trim() || !chosen" @click="applyPromo">
              应用
            </NButton>
            <NButton v-if="quote" quaternary :disabled="paying" @click="clearPromo">清除</NButton>
          </div>
          <p v-if="quote?.applied" class="promo-ok">
            <NTag size="small" :bordered="false" type="success">{{ quote.label || '促销码' }}</NTag>
            已减 {{ formatCent(quote.discount_cent) }}，实付 {{ formatCent(quote.payable_cent) }}，到账仍为
            {{ formatCent(quote.credited_cent) }}
          </p>
          <p v-else-if="quote && promoInput.trim()" class="promo-bad">{{ quote.reason || '促销码不可用' }}</p>
        </section>

        <section class="block">
          <h2>支付方式</h2>
          <div class="tile-grid">
            <button
              v-for="row in payableChannels"
              :key="row.code"
              type="button"
              class="tile"
              :class="{ 'tile--active': row.code === channelCode }"
              :disabled="Boolean(order && order.status === 'paid')"
              @click="pickChannel(row.code)"
            >
              <span class="tile-name">{{ row.display_name }}</span>
              <span class="tile-note">{{ PAY_STYLE[row.channel_type] ?? '在线支付' }}</span>
            </button>
          </div>
          <p v-if="statusNote && !paying" class="promo-bad">{{ statusNote }}</p>
        </section>
      </div>

      <aside class="checkout-side">
        <h2>金额</h2>
        <dl class="facts">
          <dt>档位</dt>
          <dd>{{ formatCent(listPrice) }}</dd>
          <dt>赠送</dt>
          <dd>{{ formatCent(order ? order.bonus_cent : (chosen?.bonus_cent ?? 0)) }}</dd>
          <dt v-if="discountCent">促销抵扣</dt>
          <dd v-if="discountCent" class="minus">−{{ formatCent(discountCent) }}</dd>
          <dt>实付</dt>
          <dd class="strong">{{ formatCent(payableCent) }}</dd>
          <dt>支付后到账</dt>
          <dd class="strong">{{ formatCent(creditedCent) }}</dd>
        </dl>
        <template v-if="!paying">
          <NButton
            type="primary"
            size="large"
            block
            :loading="busy"
            :disabled="!canPay"
            style="margin-top: 18px"
            @click="pay"
          >
            扫码支付 {{ formatCent(payableCent) }}
          </NButton>
          <p class="muted side-note">点「扫码支付」生成订单与二维码，30 分钟内未付自动关闭。</p>
        </template>
        <template v-else>
          <div class="qr-box">
            <img v-if="qrImage" :src="qrImage" alt="支付二维码" width="224" height="224">
            <p v-else class="muted">该渠道不提供二维码，请按页面提示完成付款。</p>
            <p class="qr-caption">{{ channel?.display_name }}扫码支付</p>
          </div>
          <dl class="facts">
            <dt>商户单号</dt>
            <dd><code>{{ order?.out_trade_no }}</code></dd>
            <dt>支付倒计时</dt>
            <dd>{{ remainingMs > 0 ? countdown : '订单已超时关闭' }}</dd>
          </dl>
          <p class="status-note" :class="{ 'status-note--paid': paid }">
            {{ statusNote || '等待扫码付款，系统会自动确认结果。' }}
          </p>
          <NButton block :loading="syncing" :disabled="paid" @click="sync(false)">我已完成支付</NButton>
          <NButton block quaternary :disabled="paid" @click="backToForm('')">更换支付方式</NButton>
          <NButton v-if="paid" block type="primary" @click="done">回充值页看余额</NButton>
        </template>
      </aside>
    </div>
  </template>

  <NAlert v-else-if="wallet" type="warning">没有可结算的档位，请回充值页重新选择。</NAlert>
</template>
