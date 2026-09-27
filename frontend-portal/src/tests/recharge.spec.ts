import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider } from 'naive-ui'
import { http } from '../api/client'
import RechargeView from '../views/RechargeView.vue'

vi.mock('qrcode', () => ({
  default: { toDataURL: vi.fn(async () => 'data:image/png;base64,QRMOCK') },
}))

const WALLET = {
  balance_cent: 12050,
  transactions: [
    {
      id: 1,
      amount_cent: 10,
      channel: 'manual',
      operator_id: 2,
      note: '手动入账',
      created_at: '2026-09-26T04:00:00+00:00',
    },
  ],
  packages: [
    { id: 11, label: '标准档', amount_cent: 10000, bonus_cent: 2000 },
    { id: 12, label: '大额档', amount_cent: 50000, bonus_cent: 12000 },
  ],
  channels: [
    { code: 'alipay', display_name: '支付宝', channel_type: 'alipay', orderable: true },
    { code: 'wx', display_name: '微信支付', channel_type: 'wechat', orderable: true },
    { code: 'bank', display_name: '对公转账', channel_type: 'custom', orderable: false },
  ],
}

const PENDING = {
  out_trade_no: 'KF20260927A0001',
  status: 'pending',
  channel_name: '支付宝',
  amount_cent: 10000,
  bonus_cent: 2000,
  credited_cent: 0,
  currency: 'CNY',
  code_url: null,
  redirect_url: 'https://openapi.alipay.com/gateway.do?method=alipay.trade.page.pay',
  provider_trade_no: null,
  created_at: '2026-09-27T04:00:00+00:00',
  expires_at: '2026-09-27T04:30:00+00:00',
  paid_at: null,
}

const PAID = {
  ...PENDING,
  out_trade_no: 'KF20260927A0002',
  status: 'paid',
  credited_cent: 12000,
  redirect_url: null,
  paid_at: '2026-09-27T04:05:00+00:00',
}

function mountView() {
  const root = defineComponent({ render: () => h(NMessageProvider, null, () => h(RechargeView)) })
  return mount(root, { global: { stubs: { teleport: true } } })
}

type RouteTable = Record<string, unknown | (() => unknown)>

function requestKey(config: { method?: string; url?: string }) {
  return `${(config.method ?? 'get').toUpperCase()} ${config.url}`
}

function mockRoutes(routes: RouteTable) {
  return vi.spyOn(http, 'request').mockImplementation(async (config) => {
    const key = requestKey(config)
    if (!(key in routes)) throw new Error(`未预期的请求：${key}`)
    const entry = routes[key]
    const data = typeof entry === 'function' ? (entry as () => unknown)() : entry
    return { data: { code: 0, message: 'success', data }, status: 200 } as never
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.stubGlobal('open', vi.fn())
})

describe('充值页的在线下单', () => {
  it('未开通渠道时给空态，且不提供任何假装能下单的按钮', async () => {
    mockRoutes({
      'GET /wallet': { ...WALLET, packages: [], channels: [] },
      'GET /orders': { items: [] },
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('¥120.50')
    expect(wrapper.text()).toContain('支付通道尚未开通')
    expect(wrapper.text()).not.toContain('立即支付')
    expect(wrapper.findAll('.option-row').length).toBe(0)
    expect(wrapper.findAll('button').length).toBeLessThanOrEqual(1)
    wrapper.unmount()
  })

  it('档位和支付方式渲染成两组同款行式清单，线下转账不进来', async () => {
    const call = mockRoutes({ 'GET /wallet': WALLET, 'GET /orders': { items: [] } })
    const wrapper = mountView()
    await flushPromises()
    const groups = wrapper.findAll('.checkout-group')
    expect(groups).toHaveLength(2)
    expect(groups[0].text()).toContain('选择金额')
    expect(groups[0].findAll('.option-row')).toHaveLength(2)
    expect(groups[0].text()).toContain('赠 ¥20.00')
    expect(groups[0].text()).toContain('到账 ¥120.00')
    expect(groups[1].findAll('.option-row').map((node) => node.text())).toEqual([
      '支付宝网页跳转付款',
      '微信支付扫码付款',
    ])
    expect(wrapper.text()).not.toContain('对公转账')
    // 默认选中第一项，用户不点也能直接付，但绝不让"应付"是空的。
    expect(wrapper.findAll('.option-row--active')).toHaveLength(2)
    expect(wrapper.find('.payable').text()).toBe('应付 ¥100.00')
    expect(call).toHaveBeenCalledWith(expect.objectContaining({ url: '/orders' }))
    wrapper.unmount()
  })

  it('切换档位会跟着改应付金额', async () => {
    mockRoutes({ 'GET /wallet': WALLET, 'GET /orders': { items: [] } })
    const wrapper = mountView()
    await flushPromises()
    const amounts = wrapper.findAll('.checkout-group')[0].findAll('.option-row')
    await amounts[1].trigger('click')
    expect(wrapper.find('.payable').text()).toBe('应付 ¥500.00')
    wrapper.unmount()
  })

  it('点立即支付只上报渠道与档位，付款弹窗按返回结果渲染跳转入口', async () => {
    const call = mockRoutes({
      'GET /wallet': WALLET,
      'GET /orders': { items: [] },
      'POST /orders': { ...PENDING, code_url: 'weixin://wxpay/bizpayurl?pr=KNOWFORGE' },
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.checkout-actions button').trigger('click')
    await flushPromises()
    expect(call).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'POST',
        url: '/orders',
        data: { channel_code: 'alipay', package_id: 11 },
      })
    )
    const dialog = wrapper.text()
    expect(dialog).toContain('KF20260927A0001')
    expect(dialog).toContain('应付金额¥100.00')
    // code_url 走扫码，不该同时冒出一个"前往支付页面"的按钮。
    expect(wrapper.find('.pay-qr img').attributes('src')).toBe('data:image/png;base64,QRMOCK')
    expect(wrapper.find('.pay-redirect').exists()).toBe(false)
    wrapper.unmount()
  })

  it('没有二维码的渠道给出跳转按钮，点击后打开厂商收银台', async () => {
    mockRoutes({ 'GET /wallet': WALLET, 'GET /orders': { items: [] }, 'POST /orders': PENDING })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.checkout-actions button').trigger('click')
    await flushPromises()
    const button = wrapper.find('.pay-redirect button')
    expect(button.text()).toBe('前往支付宝')
    await button.trigger('click')
    expect(globalThis.open).toHaveBeenCalledWith(
      PENDING.redirect_url,
      '_blank',
      expect.stringContaining('noopener')
    )
    wrapper.unmount()
  })

  it('我已完成支付会去向厂商查单，确认到账后刷新余额', async () => {
    let settled = false
    const syncResult = { ...PAID, sync: { ok: true, detail: '已入账' }, balance_cent: 24050 }
    mockRoutes({
      // 入账之后视图会重新拉一次钱包，这里必须给到账后的余额，不然测不出"刷新有没有生效"。
      'GET /wallet': () => (settled ? { ...WALLET, balance_cent: 24050 } : WALLET),
      'GET /orders': { items: [] },
      'POST /orders': PENDING,
      'POST /orders/KF20260927A0001/sync': () => {
        settled = true
        return syncResult
      },
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.checkout-actions button').trigger('click')
    await flushPromises()
    const confirm = wrapper.findAll('button').find((node) => node.text() === '我已完成支付')
    expect(confirm).toBeTruthy()
    await confirm?.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('支付已确认，额度已到账')
    expect(wrapper.text()).toContain('¥240.50')
    wrapper.unmount()
  })

  it('刚下的单立刻出现在最近订单里，不用手点刷新', async () => {
    let created = false
    mockRoutes({
      'GET /wallet': WALLET,
      'GET /orders': () => ({ items: created ? [PENDING] : [] }),
      'POST /orders': () => {
        created = true
        return PENDING
      },
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('还没有下过单')
    await wrapper.find('.checkout-actions button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('KF20260927A0001')
    expect(wrapper.text()).toContain('待支付')
    wrapper.unmount()
  })

  it('待支付订单留在列表里可以回来继续，已到账的不再给按钮', async () => {
    mockRoutes({ 'GET /wallet': WALLET, 'GET /orders': { items: [PENDING, PAID] } })
    const wrapper = mountView()
    await flushPromises()
    const rows = wrapper.findAll('tbody tr')
    const payable = rows.filter((row) => row.text().includes('KF20260927A0001'))
    expect(payable).toHaveLength(1)
    expect(payable[0].text()).toContain('待支付')
    expect(payable[0].find('button').text()).toBe('继续支付')
    const done = rows.find((row) => row.text().includes('已到账'))
    expect(done?.findAll('button')).toHaveLength(0)
    wrapper.unmount()
  })

  it('入账流水按分展示为元，不出现浮点尾数', async () => {
    mockRoutes({
      'GET /wallet': { ...WALLET, balance_cent: 0, packages: [], channels: [], transactions: [{ ...WALLET.transactions[0], amount_cent: 0.1 * 100 }] },
      'GET /orders': { items: [] },
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('¥0.10')
    wrapper.unmount()
  })
})
