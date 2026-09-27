import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NDialogProvider, NFormItem, NInput, NMessageProvider, NSelect, type SelectOption } from 'naive-ui'
import RechargeView from '../views/RechargeView.vue'
import { api } from '../api/client'
import type {
  ChannelTypeSpec,
  PromoRow,
  RechargeChannelRow,
  RechargeOrderDetail,
  RechargeOrderRow,
} from '../api/types'

vi.mock('../api/client', () => ({ api: vi.fn(), errorMessage: (error: unknown) => String(error) }))

let wrapper: ReturnType<typeof mount> | undefined
afterEach(() => {
  wrapper?.unmount()
  vi.clearAllMocks()
})

const SPEC: ChannelTypeSpec = {
  channel_type: 'alipay',
  display: '支付宝',
  payable: true,
  fields: [
    {
      key: 'app_id', label: '应用 APPID', help: '开放平台控制台的应用 APPID。', secret: false, required: true,
      editable: true, default: null, options: [], max_length: 32, multiline: false,
    },
    {
      key: 'gateway_url', label: '网关地址', help: '', secret: false, required: false, editable: true,
      default: 'https://openapi.alipay.com/gateway.do', options: ['https://openapi.alipay.com/gateway.do'],
      max_length: 120, multiline: false,
    },
    {
      key: 'app_private_key', label: '应用私钥', help: '裸 base64 与 PEM 两种形态都能粘。', secret: true, required: true,
      editable: true, default: null, options: [], max_length: 8000, multiline: true,
    },
    {
      key: 'content_encrypt_key', label: '内容加密 AES 密钥', help: '', secret: true, required: false, editable: true,
      default: null, options: [], max_length: 64, multiline: false,
    },
  ],
}

const CHANNEL: RechargeChannelRow = {
  id: 7,
  code: 'alipay-web',
  display_name: '支付宝',
  channel_type: 'alipay',
  enabled: false,
  updated_at: '2026-09-26T10:00:00Z',
  credentials: [
    {
      key: 'app_id', label: '应用 APPID', secret: false, configured: true, fingerprint: 'aa11bb22cc33',
      key_version: 1, set_at: '2026-09-26T09:00:00Z', value: '2021000000000000',
    },
    {
      key: 'app_private_key', label: '应用私钥', secret: true, configured: true, fingerprint: 'dd44ee55ff66',
      key_version: 2, set_at: '2026-09-26T09:00:00Z', value: null,
    },
  ],
  configuration: {
    payable: true, complete: false, missing: ['异步通知地址'], problem: '支付宝公钥与支付宝公钥证书至少要配一项',
  },
  verification: { checked: false, passed: false, at: null, detail: null },
}


function channelWith(overrides: Partial<RechargeChannelRow>): RechargeChannelRow {
  return { ...CHANNEL, ...overrides }
}

const ORDER: RechargeOrderRow = {
  out_trade_no: 'KF20260927A0001',
  status: 'pending',
  channel_code: 'alipay-web',
  channel_name: '支付宝网页支付',
  account_id: 12,
  account_username: 'customer',
  amount_cent: 10000,
  bonus_cent: 2000,
  discount_cent: 0,
  payable_cent: 10000,
  credited_cent: 0,
  currency: 'CNY',
  code_url: null,
  redirect_url: 'https://openapi.alipay.com/gateway.do?sign=abc',
  provider_trade_no: null,
  created_at: '2026-09-27T04:00:00Z',
  expires_at: '2026-09-27T04:30:00Z',
  paid_at: null,
}

const ORDER_DETAIL: RechargeOrderDetail = {
  ...ORDER,
  status: 'paid',
  credited_cent: 12000,
  provider_trade_no: '2026092722001000000099',
  paid_at: '2026-09-27T04:05:00Z',
  events: [
    { kind: 'order_created', detail: { channel: 'alipay' }, created_at: '2026-09-27T04:00:00Z' },
    { kind: 'credited', detail: { by: 'alipay-web', credited_cent: 12000, from_status: 'pending' }, created_at: '2026-09-27T04:05:00Z' },
    { kind: 'duplicate_notify', detail: { by: 'alipay-web', status: 'paid' }, created_at: '2026-09-27T04:05:30Z' },
  ],
}

function mockBackend(channels: RechargeChannelRow[] = [CHANNEL], orders: RechargeOrderRow[] = [], promos: PromoRow[] = []) {
  vi.mocked(api).mockImplementation(async (url: string) => {
    if (url === '/recharge/packages') return { items: [] }
    if (url === '/recharge/credential-specs') return { types: [SPEC] }
    if (url === '/recharge/channels') return { items: channels }
    if (url === '/recharge/promo-codes') return { items: promos }
    if (url.startsWith('/recharge/orders?')) return { items: orders, total: orders.length, limit: 20, offset: 0 }
    if (url.startsWith('/recharge/orders/')) return ORDER_DETAIL
    if (url.endsWith('/verify')) {
      return {
        channel_id: 7,
        channel_type: 'alipay',
        mode: 'live',
        passed: false,
        checks: [{ name: '网关受理签名且应用有效', ok: false, detail: 'code=40002 sub_code=isv.invalid-signature' }],
      }
    }
    return { id: 7 }
  })
}

function mounted() {
  return mount(
    defineComponent({
      render: () => h(NMessageProvider, null, () => h(NDialogProvider, null, () => h(RechargeView))),
    }),
    { global: { stubs: { teleport: true } } }
  )
}

async function openConfigDialog() {
  mockBackend()
  const view = mounted()
  await flushPromises()
  clickButton(view, '完善配置')
  await flushPromises()
  return view
}

function clickButton(view: NonNullable<typeof wrapper>, label: string) {
  const button = view.findAll('button').find((item) => item.text() === label)
  expect(button, `找不到按钮：${label}`).toBeTruthy()
  button!.trigger('click')
}

function inputs(view: NonNullable<typeof wrapper>) {
  return view.findAllComponents(NInput)
}
function textarea(view: NonNullable<typeof wrapper>) {
  return inputs(view).find((input) => input.props('type') === 'textarea')!
}
function inputByValue(view: NonNullable<typeof wrapper>, value: string) {
  return inputs(view).find((input) => input.props('value') === value)!
}
function inputByPlaceholder(view: NonNullable<typeof wrapper>, keyword: string) {
  return inputs(view).find((input) => String(input.props('placeholder') ?? '').includes(keyword))!
}
function inputByMaxLength(view: NonNullable<typeof wrapper>, length: number) {
  return inputs(view).find((input) => input.props('maxlength') === length)!
}
function lastPatch(view: NonNullable<typeof wrapper>) {
  clickButton(view, '保存配置')
  return vi.mocked(api).mock.calls.filter(([url, config]) => url === '/recharge/channels/7' && config?.method === 'PATCH').at(-1)
}

it('表单字段与提示全部来自服务端元数据，密钥只回显指纹与版本', async () => {
  wrapper = await openConfigDialog()
  const text = wrapper.text()
  expect(text).toContain('应用 APPID')
  expect(text).toContain('开放平台控制台的应用 APPID。')
  expect(text).toContain('dd44ee55ff66')
  expect(text).toContain('版本 2')
  // 私钥永不回显：输入框从空开始，并说明留空与粘贴各自的语义。
  const privateInput = textarea(wrapper)
  expect(privateInput.props('value')).toBe('')
  expect(String(privateInput.props('placeholder'))).toContain('留空即保持不变')
  // 网关地址是枚举下拉，不让人手输。
  const gatewayOption = wrapper
    .findAllComponents({ name: 'Select' })
    .map((select) => select.props('options') as SelectOption[] | undefined)
    .find((options) => options?.some((option) => String(option.value).includes('openapi.alipay.com')))
  expect(gatewayOption).toBeTruthy()
})

it('只改显示名时 body 里不带任何凭据键（旧版会顺手把密钥清空）', async () => {
  wrapper = await openConfigDialog()
  inputByValue(wrapper, '支付宝').vm.$emit('update:value', '支付宝网页支付')
  await flushPromises()
  const call = lastPatch(wrapper)
  await flushPromises()
  expect(call?.[1]).toEqual({
    method: 'PATCH',
    data: { display_name: '支付宝网页支付', credentials: {} },
  })
})

it('填了才轮换、清空才删除，两种都只发被改动的那一项', async () => {
  wrapper = await openConfigDialog()
  textarea(wrapper).vm.$emit('update:value', '-----BEGIN PRIVATE KEY-----abc')
  await flushPromises()
  const rotated = lastPatch(wrapper)
  await flushPromises()
  expect(rotated?.[1]).toEqual({
    method: 'PATCH',
    data: { display_name: '支付宝', credentials: { app_private_key: '-----BEGIN PRIVATE KEY-----abc' } },
  })

  wrapper = await openConfigDialog()
  inputByValue(wrapper, '2021000000000000').vm.$emit('update:value', '')
  await flushPromises()
  const cleared = lastPatch(wrapper)
  await flushPromises()
  expect(cleared?.[1]).toEqual({
    method: 'PATCH',
    data: { display_name: '支付宝', credentials: { app_id: null } },
  })
})

it('列表状态直接说出缺哪几项，自检按钮把厂商结论逐条摊开', async () => {
  mockBackend()
  wrapper = mounted()
  await flushPromises()
  expect(wrapper.text()).toContain('待配置')
  expect(wrapper.text()).toContain('缺 异步通知地址')
  expect(wrapper.text()).toContain('支付宝公钥与支付宝公钥证书至少要配一项')

  clickButton(wrapper, '自检')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/recharge/channels/7/verify', { method: 'POST' })
  expect(wrapper.text()).toContain('自检未通过')
  expect(wrapper.text()).toContain('已实际调用厂商接口')
  expect(wrapper.text()).toContain('isv.invalid-signature')
})

it('字段齐了但自检没过，徽章不能显示成"就绪"（已配置不等于能收款）', async () => {
  mockBackend([
    channelWith({
      enabled: true,
      configuration: { payable: true, complete: true, missing: [], problem: null },
      verification: {
        checked: true, passed: false, at: '2026-09-26T18:00:00Z', detail: '响应验签：配的支付宝公钥与网关签名不匹配',
      },
    }),
  ])
  wrapper = mounted()
  await flushPromises()
  expect(wrapper.text()).toContain('自检未通过')
  expect(wrapper.text()).toContain('配的支付宝公钥与网关签名不匹配')
  expect(wrapper.text()).not.toContain('配置完整')
})

it('自检通过后徽章带上自检时间，未自检时明确说还没验过', async () => {
  mockBackend([
    channelWith({
      configuration: { payable: true, complete: true, missing: [], problem: null },
      verification: { checked: true, passed: true, at: '2026-09-26T18:00:00Z', detail: null },
    }),
  ])
  wrapper = mounted()
  await flushPromises()
  expect(wrapper.text()).toContain('自检通过')
  expect(wrapper.text()).toContain('自检于')
})

it('必填只标真正缺了就打不通厂商的项，有默认值的字段预选好并归到可选区', async () => {
  wrapper = await openConfigDialog()
  const required = wrapper
    .findAllComponents(NFormItem)
    .filter((item) => item.props('required'))
    .map((item) => item.props('label'))
  expect(required).toEqual(['应用 APPID', '应用私钥'])
  const gateway = wrapper
    .findAllComponents(NSelect)
    .find((select) =>
      ((select.props('options') as SelectOption[]) ?? []).some((option) => option.value === SPEC.fields[1].default)
    )!
  expect(gateway.props('value')).toBe('https://openapi.alipay.com/gateway.do')
  // 网关地址虽然没存过，但默认值已选中，不该以"必填"的姿态要求人操作。
  expect(required).not.toContain('网关地址')
  expect(wrapper.findAll('.form-divider')).toHaveLength(1)
})

it('密钥输入框不能被浏览器密码管理器自动填（admin/admin123 那种串台）', async () => {
  wrapper = await openConfigDialog()
  const privateInput = textarea(wrapper)
  expect(privateInput.props('inputProps')).toMatchObject({ autocomplete: 'off', name: 'kf-credential-app-private-key' })
  const secretText = wrapper
    .findAllComponents(NInput)
    .find((input) => input.props('type') === 'password' && input.props('inputProps')?.name === 'kf-credential-content-encrypt-key')!
  expect(secretText.props('inputProps')).toMatchObject({ autocomplete: 'new-password' })
})

it('新增渠道只收类型、代码与显示名称，凭据留到完善配置里填', async () => {
  mockBackend([])
  wrapper = mounted()
  await flushPromises()
  clickButton(wrapper, '新增渠道')
  await flushPromises()
  inputByPlaceholder(wrapper, 'alipay-web').vm.$emit('update:value', 'alipay-web')
  inputByMaxLength(wrapper, 100).vm.$emit('update:value', '支付宝网页支付')
  await flushPromises()
  clickButton(wrapper, '下一步：完善配置')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/recharge/channels', {
    method: 'POST',
    data: { code: 'alipay-web', display_name: '支付宝网页支付', channel_type: 'alipay' },
  })
})

it('订单台账列出账号、金额与状态，而不是只给运营一个单号', async () => {
  mockBackend([CHANNEL], [ORDER])
  wrapper = mounted()
  await flushPromises()
  const row = wrapper.findAll('tbody tr').find((item) => item.text().includes('KF20260927A0001'))
  expect(row?.text()).toContain('customer')
  expect(row?.text()).toContain('¥100.00')
  expect(row?.text()).toContain('¥20.00')
  expect(row?.text()).toContain('待支付')
  // 渠道列显示的是给人看的名字，不是 alipay-web 这种内部代码。
  expect(row?.text()).toContain('支付宝网页支付')
})

it('筛选条件走服务端查询参数，不在前端裁列表', async () => {
  mockBackend([CHANNEL], [ORDER])
  wrapper = mounted()
  await flushPromises()
  vi.mocked(api).mockClear()
  mockBackend([CHANNEL], [ORDER])
  const statusSelect = wrapper.findAllComponents(NSelect).at(-1)!
  statusSelect.vm.$emit('update:value', 'paid')
  await flushPromises()
  const called = vi.mocked(api).mock.calls.map(([url]) => url).filter((url) => url.startsWith('/recharge/orders?'))
  expect(called.at(-1)).toContain('status=paid')
  expect(called.at(-1)).toContain('limit=20')
  clickButton(wrapper, '待人工核对')
  await flushPromises()
  const review = vi.mocked(api).mock.calls.map(([url]) => url).filter((url) => url.startsWith('/recharge/orders?')).at(-1)
  expect(review).toContain('review=true')
})

it('订单详情把事件时间线翻成人话，入账来源直接可见', async () => {
  mockBackend([CHANNEL], [ORDER])
  wrapper = mounted()
  await flushPromises()
  clickButton(wrapper, '详情')
  await flushPromises()
  const text = wrapper.text()
  expect(text).toContain('本地建单')
  expect(text).toContain('入账')
  expect(text).toContain('重复通知')
  expect(text).toContain('2026092722001000000099')
  expect(text).toContain('"credited_cent":12000')
})

const PROMO: PromoRow = {
  id: 3,
  code: 'KFNEW100',
  label: '新用户立减',
  kind: 'amount_off',
  value: 1000,
  min_amount_cent: 5000,
  starts_at: null,
  ends_at: null,
  max_uses: 100,
  remaining_uses: 97,
  per_account_limit: 1,
  used_count: 3,
  enabled: true,
  updated_at: '2026-09-27T04:00:00Z',
}

function mockPromos(promos: PromoRow[]) {
  mockBackend([CHANNEL], [], promos)
}

it('促销码列表把规则、名额与门槛翻译成人话，不把 kind/value 直接甩给运营', async () => {
  mockPromos([PROMO, { ...PROMO, id: 4, code: 'KFPCT10', kind: 'percent', value: 10, min_amount_cent: 0, max_uses: null, remaining_uses: null, used_count: 8 }])
  wrapper = mounted()
  await flushPromises()
  const rows = wrapper.findAll('tbody tr').filter((row) => row.text().includes('KF'))
  const first = rows.find((row) => row.text().includes('KFNEW100'))!
  expect(first.text()).toContain('立减 ¥10.00')
  expect(first.text()).toContain('满 ¥50.00')
  expect(first.text()).toContain('97 / 100 剩余')
  expect(first.text()).toContain('启用中')
  const percent = rows.find((row) => row.text().includes('KFPCT10'))!
  expect(percent.text()).toContain('打 90%')
  expect(percent.text()).toContain('不限')
  expect(percent.text()).toContain('已用 8')
  // 列表里出现的是 code，不是数据库主键，运营认的是码本身。
  expect(wrapper.text()).not.toContain('"kind"')
})

it('新增促销码按优惠方式换算单位：立减走元、折扣走百分比', async () => {
  mockPromos([])
  wrapper = mounted()
  await flushPromises()
  clickButton(wrapper, '新增促销码')
  await flushPromises()
  inputByMaxLength(wrapper, 32).vm.$emit('update:value', 'kfnew100')
  inputByMaxLength(wrapper, 100).vm.$emit('update:value', '新用户立减')
  await flushPromises()
  clickButton(wrapper, '保存')
  await flushPromises()
  expect(api).toHaveBeenCalledWith(
    '/recharge/promo-codes',
    expect.objectContaining({
      method: 'POST',
      data: expect.objectContaining({ code: 'KFNEW100', kind: 'amount_off', value: 1000 }),
    })
  )
})

it('停用与删除都打在服务端上，前端不自己把行从数组里抹掉', async () => {
  mockPromos([PROMO])
  wrapper = mounted()
  await flushPromises()
  vi.mocked(api).mockClear()
  mockPromos([PROMO])
  clickButton(wrapper, '停用')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/recharge/promo-codes/3', { method: 'PATCH', data: { enabled: false } })
})
