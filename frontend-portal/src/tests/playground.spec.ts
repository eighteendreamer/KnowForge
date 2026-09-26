import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, h } from 'vue'
import { NMessageProvider } from 'naive-ui'
import { knowledgeHttp } from '../api/client'
import PlaygroundView from '../views/PlaygroundView.vue'

const KEY = 'kf_test-key'
type Wrapper = ReturnType<typeof mountView>['wrapper']

function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/console/keys', component: { template: '<div />' } },
      { path: '/console/playground', component: PlaygroundView },
    ],
  })
  const root = defineComponent({
    render: () => h(NMessageProvider, null, () => h(PlaygroundView)),
  })
  return { router, wrapper: mount(root, { global: { plugins: [router], stubs: { teleport: true } } }) }
}

const calls: Array<{ url: string; authorization: string }> = []

function mockKnowledge(handlers: Record<string, unknown | (() => unknown)>) {
  calls.length = 0
  vi.spyOn(knowledgeHttp, 'request').mockImplementation(async (config) => {
    const url = String(config.url ?? '')
    calls.push({ url, authorization: String((config.headers as Record<string, string>)?.Authorization ?? '') })
    const handler = handlers[url]
    if (handler === undefined) throw new Error(`未预期的请求 ${url}`)
    const data = typeof handler === 'function' ? handler() : handler
    return { status: 200, data: { code: 0, message: 'success', data } } as never
  })
}

// 密钥明文不进 store，界面初始为空，只有粘贴进来才会去打元数据接口。
async function pasteKey(wrapper: Wrapper) {
  const input = wrapper.find('input[type="password"]')
  await input.setValue(KEY)
  await input.trigger('blur')
  await flushPromises()
}

async function submit(wrapper: Wrapper) {
  await wrapper.find('form').trigger('submit')
  await flushPromises()
}

function searchPayload(results: unknown[], suggestedTags: string[] = []) {
  return {
    query: 'Redis 缓存穿透如何解决',
    query_rewritten: null,
    search_type_used: 'keyword',
    total: results.length,
    results,
    suggested_tags: suggestedTags,
    took_ms: 28,
  }
}

const hit = {
  id: 'chunk_1',
  doc_id: 'doc_1',
  title: 'Redis 设计与实现',
  content: '缓存穿透指查询一定不存在的数据。',
  score: 0.0328,
  score_type: 'rrf',
  score_calibrated: false,
  source: 'redis.pdf',
  page: 137,
  tags: ['Redis'],
}

beforeEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('门户检索试用页的过滤项来源', () => {
  it('粘贴密钥后从 /tags 与 /categories 取选项，不给自由输入标签和分类路径', async () => {
    mockKnowledge({
      tags: { tags: [{ name: 'Redis', count: 18, category: null }] },
      categories: {
        tree: [
          {
            name: '后端开发',
            path: '后端开发',
            count: 5,
            children: [{ name: '缓存', path: '后端开发/缓存', count: 3, children: [] }],
          },
        ],
      },
    })
    const { wrapper } = mountView()
    await pasteKey(wrapper)
    expect(calls.map((call) => call.url)).toEqual(expect.arrayContaining(['tags', 'categories']))
    for (const call of calls) expect(call.authorization).toBe(`Bearer ${KEY}`)
    // 这两个框原来是自由输入（NInput + NDynamicTags），拼错只会静默得到空结果。
    expect(wrapper.find('input[placeholder="例如 后端开发/缓存"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('自己拼一个不存在的标签不会报错')
    wrapper.unmount()
  })

  it('元数据接口失败时把原因摊开，不留下一个空下拉框', async () => {
    mockKnowledge({})
    vi.spyOn(knowledgeHttp, 'request').mockRejectedValue({
      isAxiosError: true,
      response: { status: 401, data: { code: 2002, message: '密钥无效或已吊销' }, headers: {} },
      code: 'ERR_BAD_REQUEST',
      toJSON: () => ({}),
    } as never)
    const { wrapper } = mountView()
    await pasteKey(wrapper)
    expect(wrapper.text()).toContain('密钥无效或已吊销')
    wrapper.unmount()
  })

  it('没粘贴密钥时不去打元数据接口，也不假装有选项', async () => {
    mockKnowledge({ tags: { tags: [] }, categories: { tree: [] } })
    const { wrapper } = mountView()
    await flushPromises()
    await wrapper.find('input[type="password"]').trigger('blur')
    await flushPromises()
    expect(calls).toEqual([])
    expect(wrapper.text()).toContain('粘贴 API Key 之后才能取到可选标签与分类')
    wrapper.unmount()
  })

  it('提示文字与控件同属一个插槽子节点，不会被 flex 横排挤到控件上', async () => {
    mockKnowledge({
      tags: { tags: [{ name: 'Redis', count: 18, category: null }] },
      categories: { tree: [] },
    })
    const { wrapper } = mountView()
    await pasteKey(wrapper)
    const hints = wrapper.findAll('.field-hint')
    expect(hints.length).toBeGreaterThanOrEqual(2)
    for (const hint of hints) {
      const blank = hint.element.closest('.n-form-item-blank')
      expect(blank).not.toBeNull()
      expect(blank?.querySelector(':scope > .field')).not.toBeNull()
    }
    wrapper.unmount()
  })
})

describe('门户检索试用页的结果口径', () => {
  it('不带过滤的空结果才说未覆盖', async () => {
    mockKnowledge({ search: searchPayload([]) })
    const { wrapper } = mountView()
    await pasteKey(wrapper)
    await submit(wrapper)
    expect(wrapper.text()).toContain('这才是知识库未覆盖')
    expect(wrapper.text()).not.toContain('去掉过滤')
    wrapper.unmount()
  })

  it('点标签收窄之后的空结果改说要先去掉过滤复核', async () => {
    let empty = false
    mockKnowledge({
      tags: { tags: [{ name: '缓存穿透', count: 4, category: null }] },
      categories: { tree: [] },
      search: () => (empty ? searchPayload([]) : searchPayload([hit], ['缓存穿透'])),
    })
    const { wrapper } = mountView()
    await pasteKey(wrapper)
    await submit(wrapper)
    // rrf 的 0.0328 不是“置信度 3.28%”，未校准必须说清楚。
    expect(wrapper.text()).toContain('score_calibrated')
    expect(wrapper.text()).toContain('只用于本次结果内部的相对排序')
    const chip = wrapper.findAll('.tag-chip').find((node) => node.text() === '缓存穿透')
    expect(chip).toBeTruthy()
    await chip!.trigger('click')
    empty = true
    await submit(wrapper)
    expect(wrapper.text()).toContain('挂着过滤条件（标签 缓存穿透）')
    expect(wrapper.text()).toContain('去掉过滤再跑一次')
    expect(wrapper.text()).not.toContain('这才是知识库未覆盖')
    wrapper.unmount()
  })

  it('等价 curl 指向门户自身的同源地址，而不是写死的本机端口', async () => {
    mockKnowledge({ search: searchPayload([]) })
    const { wrapper } = mountView()
    await flushPromises()
    const code = wrapper.find('pre.code-block').text()
    expect(code).toContain(`${window.location.origin}/v1/knowledge/search`)
    expect(code).not.toContain('127.0.0.1:8000')
    wrapper.unmount()
  })
})
