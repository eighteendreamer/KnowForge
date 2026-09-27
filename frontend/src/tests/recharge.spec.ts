import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NDialogProvider, NFormItem, NInput, NMessageProvider, NSelect, type SelectOption } from 'naive-ui'
import RechargeView from '../views/RechargeView.vue'
import { api } from '../api/client'
import type { ChannelTypeSpec, RechargeChannelRow } from '../api/types'

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

function mockBackend(channels: RechargeChannelRow[] = [CHANNEL]) {
  vi.mocked(api).mockImplementation(async (url: string) => {
    if (url === '/recharge/packages') return { items: [] }
    if (url === '/recharge/credential-specs') return { types: [SPEC] }
    if (url === '/recharge/channels') return { items: channels }
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
    .find((select) => ((select.props('options') as SelectOption[]) ?? []).length > 0)!
  expect(gateway.props('value')).toBe('https://openapi.alipay.com/gateway.do')
  // 网关地址虽然没存过，但默认值已选中，不该以"必填"的姿态要求人操作。
  expect(required).not.toContain('网关地址')
  expect(wrapper.findAll('.form-divider')).toHaveLength(1)
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
