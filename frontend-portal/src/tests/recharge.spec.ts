import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider } from 'naive-ui'
import { http } from '../api/client'
import RechargeView from '../views/RechargeView.vue'
import CheckoutView from '../views/CheckoutView.vue'

vi.mock('qrcode', () => ({
  default: { toDataURL: vi.fn(async () => 'data:image/png;base64,QRMOCK') },
}))

const push = vi.fn()
const replace = vi.fn()
let queryParams: Record<string, string> = {}
vi.mock('vue-router', () => ({
  useRouter: () => ({ push, replace }),
  useRoute: () => ({ query: queryParams }),
}))

const WALLET = {
  balance_cent: 12050,
  transactions: [
    { id: 1, amount_cent: 10, channel: 'manual', operator_id: 2, note: '手动入账', created_at: '2026-09-26T04:00:00+00:00' },
  ],
  packages: [
    { id: 11, label: '标准档', amount_cent: 10000, bonus_cent: 2000 },
    { id: 12, label: '大额档', amount_cent: 50000, bonus_cent: 12000 },
  ],
  channels: [
    { code: 'alipay', display_name: '支付宝网页支付', channel_type: 'alipay', orderable: true },
    { code: 'wx', display_name: '微信Native扫码', channel_type: 'wechat', orderable: true },
    { code: 'bank', display_name: '对公转账', channel_type: 'custom', orderable: false },
  ],
}

const PENDING = {
  out_trade_no: 'KF20260927A0001',
  status: 'pending',
  channel_name: '支付宝网页支付',
  channel_code: 'alipay',
  package_id: 11,
  amount_cent: 10000,
  bonus_cent: 2000,
  discount_cent: 0,
  payable_cent: 10000,
  credited_cent: 0,
  currency: 'CNY',
  code_url: 'https://qr.alipay.com/kf001',
  redirect_url: null,
  provider_trade_no: null,
  created_at: '2026-09-27T04:00:00+00:00',
  expires_at: '2026-09-27T04:30:00+00:00',
  paid_at: null,
}

const PAID = { ...PENDING, out_trade_no: 'KF20260927A0002', status: 'paid', credited_cent: 12000, payable_cent: 9000, discount_cent: 1000 }
const QUOTE = {
  applied: true,
  label: '新用户立减',
  amount_cent: 10000,
  bonus_cent: 2000,
  discount_cent: 1000,
  payable_cent: 9000,
  credited_cent: 12000,
  reason: '',
}

function mountView(component: object) {
  const root = defineComponent({ render: () => h(NMessageProvider, null, () => h(component)) })
  return mount(root, { global: { stubs: { teleport: true } } })
}

function mockRoutes(routes: Record<string, unknown | (() => unknown)>) {
  return vi.spyOn(http, 'request').mockImplementation(async (config) => {
    const key = `${(config.method ?? 'get').toUpperCase()} ${config.url}`
    if (!(key in routes)) throw new Error(`未预期的请求：${key}`)
    const entry = routes[key]
    const data = typeof entry === 'function' ? (entry as () => unknown)() : entry
    return { data: { code: 0, message: 'success', data }, status: 200 } as never
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
  push.mockClear()
  replace.mockClear()
  queryParams = {}
})

describe('充值页的档位选择', () => {
  it('未开通渠道时给空态，且不提供任何假装能下单的按钮', async () => {
    mockRoutes({
      'GET /wallet': { ...WALLET, packages: [], channels: [] },
      'GET /orders': { items: [] },
    })
    const wrapper = mountView(RechargeView)
    await flushPromises()
    expect(wrapper.text()).toContain('¥120.50')
    expect(wrapper.text()).toContain('支付通道尚未开通')
    expect(wrapper.text()).not.toContain('扫码支付')
    expect(wrapper.findAll('.tile').length).toBe(0)
    expect(wrapper.findAll('button').length).toBeLessThanOrEqual(1)
    wrapper.unmount()
  })

  it('档位是磁贴而不是表格行，点一张就带着档位进结算页', async () => {
    mockRoutes({ 'GET /wallet': WALLET, 'GET /orders': { items: [] } })
    const wrapper = mountView(RechargeView)
    await flushPromises()
    const tiles = wrapper.findAll('.tile')
    expect(tiles).toHaveLength(2)
    expect(tiles[0].text()).toContain('¥100.00')
    expect(tiles[0].text()).toContain('赠 ¥20.00')
    expect(tiles[0].text()).toContain('到账 ¥120.00')
    // 表格形态被明确否掉了：档位区里不该出现 <table>。
    expect(wrapper.findAll('.section')[0].find('table').exists()).toBe(false)
    await tiles[1].trigger('click')
    expect(push).toHaveBeenCalledWith({ name: 'recharge-checkout', query: { package: '12' } })
    wrapper.unmount()
  })

  it('待支付订单给"继续支付"，并且是接着付同一张单而不是下一张新单', async () => {
    mockRoutes({ 'GET /wallet': WALLET, 'GET /orders': { items: [PENDING, PAID] } })
    const wrapper = mountView(RechargeView)
    await flushPromises()
    const rows = wrapper.findAll('tbody tr')
    const open = rows.find((row) => row.text().includes('KF20260927A0001'))!
    expect(open.text()).toContain('待支付')
    expect(open.text()).toContain('¥100.00')
    await open.find('button').trigger('click')
    expect(push).toHaveBeenCalledWith({
      name: 'recharge-checkout',
      query: { order: 'KF20260927A0001' },
    })
    const done = rows.find((row) => row.text().includes('KF20260927A0002'))!
    expect(done.text()).toContain('已到账')
    expect(done.findAll('button')).toHaveLength(0)
    wrapper.unmount()
  })

  it('入账流水按分展示为元，不出现浮点尾数', async () => {
    mockRoutes({
      'GET /wallet': { ...WALLET, balance_cent: 0, packages: [], channels: [], transactions: [{ ...WALLET.transactions[0], amount_cent: 0.1 * 100 }] },
      'GET /orders': { items: [] },
    })
    const wrapper = mountView(RechargeView)
    await flushPromises()
    expect(wrapper.text()).toContain('¥0.10')
    wrapper.unmount()
  })
})

describe('结算页', () => {
  it('展示订单金额与可支付方式，线下转账渠道不进来', async () => {
    queryParams = { package: '11' }
    mockRoutes({ 'GET /wallet': WALLET, 'GET /orders/KF20260927A0001': PENDING })
    const wrapper = mountView(CheckoutView)
    await flushPromises()
    expect(wrapper.text()).toContain('标准档')
    expect(wrapper.text()).toContain('实付¥100.00')
    expect(wrapper.text()).toContain('支付后到账¥120.00')
    const methods = wrapper.findAll('.checkout-main .tile')
    expect(methods.map((node) => node.text())).toEqual(['支付宝网页支付用支付宝扫码', '微信Native扫码用微信扫码'])
    expect(wrapper.text()).not.toContain('对公转账')
    expect(wrapper.find('.qr-box').exists()).toBe(false)
    wrapper.unmount()
  })

  it('促销码可用时改实付不改到账，不可用时把厂商侧原因显示出来', async () => {
    queryParams = { package: '11' }
    const rejected = { ...QUOTE, applied: false, discount_cent: 0, payable_cent: 10000, reason: '促销码名额已用完' }
    vi.spyOn(http, 'request').mockImplementation(async (config) => {
      const data =
        config.url === '/promo/quote'
          ? (config.data as { promo_code: string }).promo_code === 'KFBAD'
            ? rejected
            : QUOTE
          : WALLET
      return { data: { code: 0, message: 'success', data }, status: 200 } as never
    })
    const wrapper = mountView(CheckoutView)
    await flushPromises()
    const input = wrapper.find('input')
    await input.setValue('KFNEW100')
    await wrapper.findAll('.promo-row button')[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.promo-ok').text()).toContain('已减 ¥10.00')
    expect(wrapper.find('.order-amount').text()).toContain('¥90.00')
    expect(wrapper.find('.order-amount').text()).toContain('¥100.00')
    expect(wrapper.text()).toContain('支付后到账¥120.00')

    await input.setValue('KFBAD')
    await wrapper.findAll('.promo-row button')[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.promo-bad').text()).toContain('促销码名额已用完')
    expect(wrapper.find('.order-amount').text()).toContain('¥100.00')
    wrapper.unmount()
  })

  it('点扫码支付才真正下单，并在同一页切到二维码步骤', async () => {
    queryParams = { package: '11' }
    const call = mockRoutes({
      'GET /wallet': WALLET,
      'POST /orders': PENDING,
    })
    const wrapper = mountView(CheckoutView)
    await flushPromises()
    call.mockClear()
    mockRoutes({ 'GET /wallet': WALLET, 'POST /orders': PENDING })
    const payButton = wrapper.findAll('.checkout-side button').find((node) => node.text().includes('扫码支付'))!
    expect(payButton.text()).toContain('¥100.00')
    await payButton.trigger('click')
    await flushPromises()
    expect(call).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'POST',
        url: '/orders',
        data: { channel_code: 'alipay', package_id: 11, promo_code: '' },
      })
    )
    expect(wrapper.find('.qr-box img').attributes('src')).toBe('data:image/png;base64,QRMOCK')
    expect(wrapper.text()).toContain('KF20260927A0001')
    expect(wrapper.text()).toContain('支付宝网页支付扫码支付')
    expect(wrapper.text()).toContain('订单已生成，请扫码完成付款')
    wrapper.unmount()
  })

  it('换支付方式会把已生成的单退回选择步骤，避免两张码同时可扫', async () => {
    queryParams = { package: '11' }
    mockRoutes({ 'GET /wallet': WALLET, 'POST /orders': PENDING })
    const wrapper = mountView(CheckoutView)
    await flushPromises()
    await wrapper.findAll('.checkout-side button').find((node) => node.text().includes('扫码支付'))!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.qr-box').exists()).toBe(true)
    await wrapper.findAll('.checkout-main .tile')[1].trigger('click')
    expect(wrapper.find('.qr-box').exists()).toBe(false)
    expect(wrapper.text()).toContain('已切换支付方式，请重新下单')
    wrapper.unmount()
  })

  it('我已完成支付去向厂商查单，确认后刷新到账', async () => {
    queryParams = { package: '11' }
    let settled = false
    mockRoutes({
      'GET /wallet': () => (settled ? { ...WALLET, balance_cent: 24050 } : WALLET),
      'POST /orders': PENDING,
      'POST /orders/KF20260927A0001/sync': () => {
        settled = true
        return { ...PAID, sync: { ok: true, detail: '已入账' }, balance_cent: 24050 }
      },
    })
    const wrapper = mountView(CheckoutView)
    await flushPromises()
    await wrapper.findAll('.checkout-side button').find((node) => node.text().includes('扫码支付'))!.trigger('click')
    await flushPromises()
    const confirm = wrapper.findAll('.checkout-side button').find((node) => node.text() === '我已完成支付')!
    await confirm.trigger('click')
    await flushPromises()
    expect(wrapper.find('.status-note').text()).toContain('支付已确认，额度已到账')
    expect(wrapper.find('.status-note--paid').exists()).toBe(true)
    wrapper.unmount()
  })

  it('从最近订单进来时直接回到那张单的二维码，不再下一单', async () => {
    queryParams = { order: 'KF20260927A0001' }
    const call = mockRoutes({ 'GET /wallet': WALLET, 'GET /orders/KF20260927A0001': PENDING })
    const wrapper = mountView(CheckoutView)
    await flushPromises()
    expect(wrapper.find('.qr-box img').exists()).toBe(true)
    expect(wrapper.text()).toContain('支付宝网页支付扫码支付')
    expect(call.mock.calls.map(([config]) => config.url)).not.toContain('/orders')
    wrapper.unmount()
  })

  it('档位已下架时回充值页，而不是留一个空白结算页', async () => {
    queryParams = { package: '999' }
    mockRoutes({ 'GET /wallet': WALLET })
    const wrapper = mountView(CheckoutView)
    await flushPromises()
    expect(replace).toHaveBeenCalledWith({ name: 'recharge' })
    wrapper.unmount()
  })
})
