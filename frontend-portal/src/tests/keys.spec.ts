import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, h } from 'vue'
import { NDialogProvider, NMessageProvider } from 'naive-ui'
import { http } from '../api/client'
import KeysView from '../views/KeysView.vue'

const routes = [
  { path: '/console/keys', component: KeysView },
  { path: '/console/usage', component: { template: '<div />' } },
]

function mountView() {
  const router = createRouter({ history: createMemoryHistory(), routes })
  const root = defineComponent({
    render: () => h(NMessageProvider, null, () => h(NDialogProvider, null, () => h(KeysView))),
  })
  return { router, wrapper: mount(root, { global: { plugins: [router], stubs: { teleport: true } } }) }
}

const rows = [
  {
    id: 7,
    name: '生产密钥',
    key_prefix: 'kf_abcd',
    status: 'active',
    scopes: ['knowledge:read'],
    rate_limit_per_day: 1000,
    rate_limit_per_minute: 60,
    total_calls: 12,
    expires_at: null,
    created_at: '2026-09-26T04:00:00+00:00',
    last_used_at: null,
  },
  {
    id: 8,
    name: '已吊销',
    key_prefix: 'kf_zzzz',
    status: 'revoked',
    scopes: ['knowledge:read'],
    rate_limit_per_day: 1000,
    rate_limit_per_minute: 60,
    total_calls: 0,
    expires_at: null,
    created_at: '2026-09-20T04:00:00+00:00',
    last_used_at: null,
  },
]

beforeEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('门户 API Key 页', () => {
  it('列表只读展示配额，不提供改配额的入口', async () => {
    vi.spyOn(http, 'request').mockResolvedValue({ data: { code: 0, data: { items: rows, total: 2 } } })
    const { wrapper } = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('生产密钥')
    expect(wrapper.text()).toContain('60 / 1000')
    expect(wrapper.text()).toContain('尚未使用')
    expect(wrapper.find('input[name="rate_limit"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('创建成功后明文只显示一次，并可一键填入检索试用', async () => {
    const calls: Array<{ url: string; method: string }> = []
    vi.spyOn(http, 'request').mockImplementation(async (config) => {
      calls.push({ url: config.url ?? '', method: String(config.method ?? 'get').toLowerCase() })
      if (String(config.method).toLowerCase() === 'post') {
        return { data: { code: 0, data: { ...rows[0], id: 9, key: 'kf_plain-only-once' } } } as never
      }
      return { data: { code: 0, data: { items: rows, total: 2 } } } as never
    })
    const { wrapper } = mountView()
    await flushPromises()
    // 打开创建弹窗、填名、保存，验证明文只在响应里出现一次。
    await wrapper.findAll('button').find((button) => button.text() === '创建密钥')!.trigger('click')
    const nameInput = wrapper.find('input[maxlength="100"]')
    await nameInput.setValue('新的密钥')
    await wrapper.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
    await flushPromises()
    expect(calls).toContainEqual({ url: '/keys', method: 'post' })
    expect(wrapper.text()).toContain('密钥明文只显示这一次')
    const fill = wrapper.findAll('button').find((button) => button.text() === '填入检索试用')
    expect(fill).toBeTruthy()
    await fill!.trigger('click')
    expect(sessionStorage.getItem('knowforge.portal.playgroundKey')).toBe('kf_plain-only-once')
    wrapper.unmount()
  })

  it('已吊销的密钥不再提供编辑、启用与吊销操作', async () => {
    vi.spyOn(http, 'request').mockResolvedValue({ data: { code: 0, data: { items: [rows[1]], total: 1 } } })
    const { wrapper } = mountView()
    await flushPromises()
    const actions = wrapper.text()
    expect(actions).toContain('已吊销')
    expect(actions).toContain('调用记录')
    expect(actions).not.toContain('编辑')
    expect(actions).not.toContain('启用')
    wrapper.unmount()
  })
})
