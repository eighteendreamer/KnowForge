import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, h } from 'vue'
import { createPinia } from 'pinia'
import { NDialogProvider, NMessageProvider } from 'naive-ui'
import { http, setAccessToken } from '../api/client'
import ConsoleLayout from '../components/ConsoleLayout.vue'
import KeysView from '../views/KeysView.vue'

const realMatchMedia = window.matchMedia

function pretendViewport(narrow: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: narrow,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

function buildRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/login', component: { template: '<div />' } },
      { path: '/register', component: { template: '<div />' } },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ],
  })
}

function mountShell(component: object, router = buildRouter()) {
  const root = defineComponent({
    render: () => h(NMessageProvider, null, () => h(NDialogProvider, null, () => h(component))),
  })
  return { router, wrapper: mount(root, { global: { plugins: [router, createPinia()], stubs: { teleport: true } } }) }
}

const keyRow = {
  id: 7,
  name: '生产密钥',
  key_prefix: 'kf_g050WYT4_',
  status: 'active',
  scopes: ['knowledge:read'],
  rate_limit_per_day: 1000,
  rate_limit_per_minute: 60,
  total_calls: 4,
  expires_at: null,
  created_at: '2026-09-26T11:00:00+00:00',
  last_used_at: '2026-09-26T11:14:46+00:00',
}

beforeEach(() => {
  sessionStorage.clear()
  setAccessToken('')
  vi.restoreAllMocks()
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: realMatchMedia,
  })
})

describe('控制台外壳的窄屏形态', () => {
  it('宽屏用常驻侧栏，不放汉堡按钮', async () => {
    pretendViewport(false)
    const { wrapper } = mountShell(ConsoleLayout)
    await flushPromises()
    expect(wrapper.find('.n-layout-sider').exists()).toBe(true)
    expect(wrapper.find('.gate-burger').exists()).toBe(false)
    wrapper.unmount()
  })

  it('窄屏收掉侧栏，汉堡按钮打开抽屉后能看到全部 7 项导航', async () => {
    pretendViewport(true)
    const { wrapper } = mountShell(ConsoleLayout)
    await flushPromises()
    expect(wrapper.find('.n-layout-sider').exists()).toBe(false)
    const burger = wrapper.find('.gate-burger')
    expect(burger.exists()).toBe(true)
    await burger.trigger('click')
    await flushPromises()
    const text = wrapper.text()
    for (const label of ['数据概览', 'API Key', '调用记录', '检索试用', '接口文档', '充值', '账号设置']) {
      expect(text).toContain(label)
    }
    wrapper.unmount()
  })

  // 顶栏统一：控制台不再自带一套 header，直接渲染欢迎页那个 header.gate-header。
  it('控制台顶栏与欢迎页是同一个组件，样式只有一份', async () => {
    pretendViewport(false)
    const { wrapper } = mountShell(ConsoleLayout)
    await flushPromises()
    const header = wrapper.find('header.gate-header')
    expect(header.exists()).toBe(true)
    expect(header.text()).toContain('技术知识检索开放平台')
    expect(header.text()).toContain('系统介绍')
    expect(wrapper.find('.console-frame').exists()).toBe(true)
    wrapper.unmount()
  })

  it('账号区用同一个下拉，控制台里不再列"控制台"这一项', async () => {
    setAccessToken('portal-token')
    vi.spyOn(http, 'request').mockResolvedValue({
      data: { code: 0, data: { id: 4, username: 'admin1_', role: 'end_user', status: 'active' } },
    } as never)
    pretendViewport(false)
    const { wrapper } = mountShell(ConsoleLayout)
    await flushPromises()
    const account = wrapper.find('button.account')
    expect(account.exists()).toBe(true)
    expect(account.text()).toContain('admin1_')
    await account.trigger('click')
    await flushPromises()
    // hide-console 生效时，下拉里只剩"退出登录"一项；欢迎页那份会多一项"控制台"。
    expect(wrapper.findAll('.n-dropdown-option').map((item) => item.text().trim())).toEqual(['退出登录'])
    wrapper.unmount()
  })
})

describe('API Key 列表的窄屏列集', () => {
  function headers(wrapper: ReturnType<typeof mountShell>['wrapper']) {
    return wrapper.findAll('thead th').map((cell) => cell.text().trim())
  }

  it('窄屏只留三列并且不再横向滚动 1120px', async () => {
    pretendViewport(true)
    vi.spyOn(http, 'request').mockResolvedValue({
      data: { code: 0, data: { items: [keyRow], total: 1 } },
    } as never)
    const { wrapper } = mountShell(KeysView)
    await flushPromises()
    expect(headers(wrapper)).toEqual(['密钥', '状态', '操作'])
    expect(wrapper.text()).toContain('kf_g050WYT4_ · 60/分 · 累计 4')
    expect(wrapper.find('table').attributes('style') ?? '').not.toContain('1120')
    wrapper.unmount()
  })

  it('宽屏保留完整八列，操作收进"更多"菜单而不是四个并排按钮', async () => {
    pretendViewport(false)
    vi.spyOn(http, 'request').mockResolvedValue({
      data: { code: 0, data: { items: [keyRow], total: 1 } },
    } as never)
    const { wrapper } = mountShell(KeysView)
    await flushPromises()
    expect(headers(wrapper)).toEqual([
      '名称',
      '密钥前缀',
      '状态',
      '配额（分钟 / 日）',
      '累计调用',
      '到期时间',
      '最后使用',
      '操作',
    ])
    const actions = wrapper.find('.row-actions')
    expect(actions.findAll('button').map((button) => button.text())).toEqual(['调用记录', '更多'])
    wrapper.unmount()
  })
})
