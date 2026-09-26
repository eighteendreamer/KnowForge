import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'
import { http, publicHttp, setAccessToken } from '../api/client'
import WelcomeView from '../views/WelcomeView.vue'

const CURL = "curl -sS http://x/v1/knowledge/search -H 'Authorization: Bearer $KF_KEY' -d '{\"query\":\"Redis\"}'"
const PY = "AsyncKnowForge(api_key='kf_x').search('Redis')"

// 容量数字全部由 GET /api-docs 提供，这里故意写成哨兵，用来证明页面没有自己抄一份。
const docs = {
  base_url: 'http://x/v1/knowledge',
  version: '0.1.0',
  auth: 'Bearer',
  envelope: { success: '{"code":0}', error: '{"code":1001}' },
  error_codes: [
    { code: 1001, http: 400, meaning: '参数错误' },
    { code: 3001, http: 429, meaning: '超配额' },
  ],
  rate_limits: {
    scope: '按密钥计量',
    counters: [
      { name: 'rate_limit_per_minute', default: 60, unit: '次/分钟', meaning: '令牌桶' },
      { name: 'rate_limit_per_day', default: 1000, unit: '次/天', meaning: '日配额' },
    ],
    on_exceed: { http: 429, code: 3001, header: 'Retry-After（秒）', client_rule: '退避重试' },
    shared_budget: '与离线任务共用预算',
    quota_note: '频控不计费',
  },
  performance: {
    measure: '单节点实测，口径见服务端返回',
    throughput: [{ scenario: '缓存命中检索', sustainable: 'SENTINEL-THROUGHPUT', p95: 'SENTINEL-P95', note: '0 错误' }],
    latency: [{ scenario: 'keyword + 精排', value: 'SENTINEL-LATENCY', note: '精排是远程调用' }],
    bottleneck: '瓶颈在事件循环',
  },
  quickstart: [{ step: 1, title: '建钥', detail: '控制台创建只读密钥' }],
  sdks: [
    {
      language: 'python',
      package: 'knowforge-sdk',
      install: 'pip install knowforge-sdk',
      requires: 'Python 3.11+',
      client: 'AsyncKnowForge()',
      methods: ['search()'],
      note: '返回 data',
    },
  ],
  changelog: [{ date: '2026-09-26', changes: ['补齐响应字段表'] }],
  endpoints: [
    {
      method: 'POST',
      path: '/v1/knowledge/search',
      summary: '四模式知识检索',
      purpose: '在已入库文档中检索分块。',
      notes: ['query 1~500 字。'],
      auth_required: true,
      request_fields: [],
      response_fields: [{ field: 'search_type_used', type: 'string', meaning: '实际生效模式' }],
      result_fields: [
        { field: 'score_calibrated', type: 'boolean', meaning: '分数是否已校准' },
        { field: 'source', type: 'string', meaning: '原始文件名' },
      ],
      response_examples: { success: '{"code":0,"data":{"total":24,"results":[]}}' },
      examples: [
        { language: 'curl', code: CURL },
        { language: 'python', code: PY },
      ],
      responses: ['200'],
    },
    {
      method: 'GET',
      path: '/v1/knowledge/tags',
      summary: '已过审标签',
      purpose: '取可过滤标签。',
      notes: [],
      auth_required: true,
      request_fields: [],
      response_fields: [],
      result_fields: [],
      response_examples: {},
      examples: [],
      responses: ['200'],
    },
  ],
}

function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: WelcomeView },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ],
  })
  const root = defineComponent({ render: () => h(NMessageProvider, null, () => h(WelcomeView)) })
  return mount(root, { global: { plugins: [router, createPinia()], stubs: { teleport: true } } })
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  setAccessToken('')
  vi.restoreAllMocks()
  vi.spyOn(http, 'request').mockResolvedValue({
    data: { code: 0, data: { id: 3, username: 'lulu', role: 'end_user', status: 'active' } },
  } as never)
})

describe('门户欢迎页的口径', () => {
  it('容量与延迟只有一处来源，正文里不再抄写具体毫秒数', async () => {
    vi.spyOn(publicHttp, 'get').mockResolvedValue({ status: 200, data: { code: 0, data: docs } } as never)
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('SENTINEL-THROUGHPUT')
    expect(text).toContain('SENTINEL-LATENCY')
    expect(text).toContain('单节点实测，口径见服务端返回')
    for (const stale of ['352', '1424', '91 ms', '60 req/s', '62 ms']) expect(text).not.toContain(stale)
    // 版本号现在在首屏底部的数据条里，与接口数/错误码数并排。
    expect(text).toContain('接口版本')
    expect(text).toContain('0.1.0')
    wrapper.unmount()
  })

  it('返回字段表与代码面板都取自 api-docs 的实时输出', async () => {
    vi.spyOn(publicHttp, 'get').mockResolvedValue({ status: 200, data: { code: 0, data: docs } } as never)
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('POST /v1/knowledge/search')
    expect(text).toContain(CURL)
    expect(text).toContain('响应示例')
    // 字段表来自接口的 result_fields，不是页面手写的一份。
    expect(text).toContain('score_calibrated')
    expect(text).toContain('原始文件名')
    wrapper.unmount()
  })

  it('标签过滤与空结果的边界写清楚，不把过滤后的空当成未覆盖', async () => {
    vi.spyOn(publicHttp, 'get').mockResolvedValue({ status: 200, data: { code: 0, data: docs } } as never)
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('自己拼一个不存在或未过审的标签不会报错')
    expect(text).toContain('去掉过滤复核一次再下结论')
    expect(text).toContain('不同模式之间不可比较')
    wrapper.unmount()
  })

  it('落地页隐藏浏览器滚动条，离开后恢复', async () => {
    vi.spyOn(publicHttp, 'get').mockResolvedValue({ status: 200, data: { code: 0, data: docs } } as never)
    expect(document.documentElement.classList.contains('hide-scrollbar')).toBe(false)
    const wrapper = mountView()
    await flushPromises()
    expect(document.documentElement.classList.contains('hide-scrollbar')).toBe(true)
    wrapper.unmount()
    expect(document.documentElement.classList.contains('hide-scrollbar')).toBe(false)
  })

  it('接口数据拉不到时明说未加载，不留空表格', async () => {
    vi.spyOn(publicHttp, 'get').mockRejectedValue(new Error('后端没起来'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('容量与接口数据暂未加载')
    expect(wrapper.text()).toContain('接口清单未加载：后端没起来')
    expect(wrapper.text()).toContain('接口清单以 GET /v1/knowledge/api-docs 实时输出为准')
    wrapper.unmount()
  })
})

describe('门户顶栏', () => {
  it('未登录时只有系统介绍与登录入口，不再有四个锚点', async () => {
    vi.spyOn(publicHttp, 'get').mockResolvedValue({ status: 200, data: { code: 0, data: docs } } as never)
    const wrapper = mountView()
    await flushPromises()
    const nav = wrapper.find('nav.gate-nav')
    expect(nav.text()).toContain('系统介绍')
    expect(nav.text()).toContain('免费注册')
    for (const retired of ['检索模式', '实测容量', '接入方式']) expect(nav.text()).not.toContain(retired)
    expect(nav.find('button.account').exists()).toBe(false)
    wrapper.unmount()
  })

  it('登录后换成头像 + 昵称，下拉里是控制台与退出登录', async () => {
    setAccessToken('portal-token')
    vi.spyOn(publicHttp, 'get').mockResolvedValue({ status: 200, data: { code: 0, data: docs } } as never)
    const wrapper = mountView()
    await flushPromises()
    const account = wrapper.find('button.account')
    expect(account.exists()).toBe(true)
    expect(account.text()).toContain('lulu')
    expect(account.text()).toContain('L')
    expect(wrapper.find('nav.gate-nav').text()).not.toContain('免费注册')
    await account.trigger('click')
    await flushPromises()
    const menu = wrapper.text()
    expect(menu).toContain('控制台')
    expect(menu).toContain('退出登录')
    wrapper.unmount()
  })
})
