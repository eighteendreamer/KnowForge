<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NAlert, NButton, NIcon, NInput, useMessage } from 'naive-ui'
import { ArrowBackOutline, BookOutline, CheckmarkCircleOutline, EllipseOutline } from '@vicons/ionicons5'
import QRCode from 'qrcode'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage, formatCent } from '../api/client'
import { useAction } from '../api/feedback'
import type { OrderSyncData, PaymentOrderData, PromoQuote, WalletData } from '../api/types'

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
const promoOpen = ref(false)
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
const payableCent = computed(() =>
  order.value ? order.value.payable_cent : (quote.value?.payable_cent ?? chosen.value?.amount_cent ?? 0)
)
const creditedCent = computed(() =>
  order.value
    ? order.value.amount_cent + order.value.bonus_cent
    : quote.value
      ? quote.value.credited_cent
      : (chosen.value?.amount_cent ?? 0) + (chosen.value?.bonus_cent ?? 0)
)
const bonusCent = computed(() =>
  order.value ? order.value.bonus_cent : (quote.value?.bonus_cent ?? chosen.value?.bonus_cent ?? 0)
)
const discountCent = computed(() =>
  order.value ? order.value.discount_cent : (quote.value?.discount_cent ?? 0)
)
const listPrice = computed(() =>
  order.value ? order.value.amount_cent : (chosen.value?.amount_cent ?? 0)
)
const promoRejected = computed(() =>
  quote.value && !quote.value.applied ? quote.value.reason || '促销码不可用' : ''
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
    // 用上了就把它折成一行金额，输入框收回原处，结算页保持"一行项目一个数"。
    if (quote.value.applied) promoOpen.value = false
    else if (promoInput.value.trim()) message.warning(quote.value.reason || '促销码不可用')
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
  promoOpen.value = false
}

function pickChannel(code: string) {
  channelCode.value = code
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
    await showOrder(created)
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
  <div class="checkout">
    <header class="checkout-brand">
      <NButton text class="checkout-back" aria-label="返回充值页" @click="done">
        <template #icon>
          <NIcon :size="18"><ArrowBackOutline /></NIcon>
        </template>
      </NButton>
      <span class="checkout-mark">
        <NIcon :size="22" color="#18a058"><BookOutline /></NIcon>
      </span>
      <div class="checkout-merchant">
        <strong>KnowForge</strong>
        <span class="muted">技术知识检索开放平台</span>
      </div>
    </header>

    <template v-if="wallet && (chosen || order)">
      <div class="checkout-grid">
        <section class="checkout-main">
          <h2 class="checkout-title">向 KnowForge 支付</h2>
          <p class="muted checkout-lede">核对金额与支付方式，扫码付款后额度即时到账。</p>

          <ul class="line-items">
            <li>
              <span class="line-name">{{ chosen?.label ?? '已生成的订单' }}<small class="muted">在线充值</small></span>
              <span class="line-value">{{ formatCent(listPrice) }}</span>
            </li>
            <li v-if="bonusCent">
              <span class="line-name">本档赠送<small class="muted">与充值一并到账</small></span>
              <span class="line-value">{{ formatCent(bonusCent) }}</span>
            </li>
            <li v-if="discountCent">
              <span class="line-name">促销码<small v-if="quote?.label" class="muted">{{ quote.label }}</small></span>
              <span class="line-value minus">−{{ formatCent(discountCent) }}</span>
            </li>
          </ul>

          <div class="totals">
            <div class="total-row">
              <span class="total-label">小计</span>
              <span class="total-value">{{ formatCent(listPrice) }}</span>
            </div>

            <div v-if="!paying && !promoOpen" class="total-row total-row--promo">
              <button
                v-if="!discountCent"
                type="button"
                class="pill"
                :disabled="!chosen"
                @click="promoOpen = true"
              >
                添加促销码
              </button>
              <span v-else class="total-label">
                <span class="pill pill--applied">促销码已应用</span>
                <button type="button" class="link" @click="clearPromo">移除</button>
              </span>
            </div>

            <form v-if="promoOpen && !paying && !discountCent" class="promo-row" @submit.prevent="applyPromo">
              <NInput
                v-model:value="promoInput"
                :maxlength="32"
                placeholder="输入促销码"
                @update:value="quote = null"
              />
              <NButton type="primary" :loading="promoBusy" :disabled="!promoInput.trim()" @click="applyPromo">
                应用
              </NButton>
              <NButton text @click="clearPromo">取消</NButton>
            </form>

            <div class="total-row total-row--grand">
              <span class="total-label">应付合计</span>
              <span class="total-value order-amount">
                <span v-if="discountCent" class="strike muted">{{ formatCent(listPrice) }}</span>
                <strong>{{ formatCent(payableCent) }}</strong>
              </span>
            </div>
            <div class="total-row total-row--credited">
              <span class="total-label">支付后到账</span>
              <span class="total-value">{{ formatCent(creditedCent) }}</span>
            </div>
          </div>

          <p v-if="promoRejected" class="promo-bad">{{ promoRejected }}</p>
        </section>

        <aside class="checkout-side">
          <h2>{{ paying ? '请扫码付款' : '选择支付方式' }}</h2>

          <div v-if="!paying" class="pay-methods">
            <button
              v-for="row in payableChannels"
              :key="row.code"
              type="button"
              class="pay-method"
              :class="{ 'pay-method--active': row.code === channelCode }"
              @click="pickChannel(row.code)"
            >
              <NIcon class="pay-method-dot" :size="18" :color="row.code === channelCode ? '#18a058' : '#c9c9ce'">
                <CheckmarkCircleOutline v-if="row.code === channelCode" />
                <EllipseOutline v-else />
              </NIcon>
              <span class="pay-method-name">{{ row.display_name }}</span>
              <span class="pay-method-note">{{ PAY_STYLE[row.channel_type] ?? '在线支付' }}</span>
            </button>
          </div>

          <template v-if="!paying">
            <p v-if="statusNote" class="promo-bad">{{ statusNote }}</p>
            <NButton
              class="pay-submit"
              type="primary"
              size="large"
              block
              :loading="busy"
              :disabled="!canPay"
              @click="pay"
            >
              扫码支付 {{ formatCent(payableCent) }}
            </NButton>
            <p class="muted side-note">点击后生成订单与二维码，30 分钟内未付自动关闭。</p>
          </template>

          <template v-else>
            <div class="qr-box">
              <img v-if="qrImage" :src="qrImage" alt="支付二维码" width="224" height="224">
              <p v-else class="muted">该渠道不提供二维码，请按页面提示完成付款。</p>
              <p class="qr-caption">{{ channel?.display_name ?? order?.channel_name }}扫码支付</p>
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
            <NButton block quaternary :disabled="paid" @click="backToForm('这张单在超时前仍然有效，换方式后请勿扫描旧二维码')">
              更换支付方式
            </NButton>
            <NButton v-if="paid" block type="primary" @click="done">回充值页看余额</NButton>
          </template>
        </aside>
      </div>
    </template>

    <NAlert v-else-if="wallet" type="warning">没有可结算的档位，请回充值页重新选择。</NAlert>
  </div>
</template>
