import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { NMessageProvider } from 'naive-ui'
import SearchView from '../views/SearchView.vue'
import { api } from '../api/client'

vi.mock('../api/client', () => ({ api: vi.fn() }))
let wrapper: ReturnType<typeof mount> | undefined
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks() })

it('renders string section paths from the API and supports preview, judgment and empty results', async () => {
  const result = { id: 'chunk_test', doc_id: 'doc_test', title: 'Redis 缓存', content: '使用布隆过滤器', highlight: '使用<em>布隆过滤器</em>', score: 0.91, score_type: 'rerank', score_calibrated: false, source: 'redis.html', page: null, section: 'Redis缓存实践 > 缓存穿透', tags: [], category: '后端开发', difficulty: '中级' }
  let results = [result]
  vi.mocked(api).mockImplementation(async (url) => {
    if (url === '/search') return { query: '缓存穿透', query_rewritten: null, search_type_used: 'hybrid', results, total: results.length, suggested_tags: [], took_ms: 200 }
    if (url === '/evaluations') return {}
    return { items: [], total: 0 }
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/search', component: SearchView }] })
  await router.push('/search')
  wrapper = mount(defineComponent({ render: () => h(NMessageProvider, null, () => h(SearchView)) }), {
    global: { plugins: [router], stubs: { teleport: true, DocumentPreview: true } },
  })
  await flushPromises()
  await wrapper.get('input[aria-label="检索问题"]').setValue('缓存穿透')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.findAll('.search-result')).toHaveLength(1)
  expect(wrapper.text()).toContain(result.section)
  expect(wrapper.get('mark').text()).toBe('布隆过滤器')
  expect(wrapper.text()).toContain('当前分数未校准')
  await wrapper.findAll('button').find((button) => button.text() === '查看原文')!.trigger('click')
  await flushPromises()
  expect(wrapper.findComponent({ name: 'DocumentPreview' }).props('docId')).toBe('doc_test')
  await wrapper.findAll('button').find((button) => button.text() === '标注相关性')!.trigger('click')
  await flushPromises()
  await wrapper.findAll('button').find((button) => button.text() === '保存标注')!.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/evaluations', { method: 'POST', data: { query: '缓存穿透', chunk_id: 'chunk_test', judgment: 2, notes: null } })
  results = []
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper.findAll('.search-result')).toHaveLength(0)
  expect(wrapper.text()).toContain('没有匹配的知识片段')
})

it('renders one merged adjacent-chunk hit as a single markdown block without repeating the overlap', async () => {
  // 后端 merge_adjacent 默认开启：同文档相邻分块并成一条，重叠区已去重，正文里同一段话不应出现两次。
  const merged = {
    id: 'chunk_a',
    merged_ids: ['chunk_b'],
    doc_id: 'doc_test',
    title: 'Redis 缓存',
    content: '## 缓存穿透\n\n使用布隆过滤器拦截空值查询。布隆过滤器需要预估容量并支持删除。',
    highlight: '使用<em>布隆过滤器</em>',
    score: 0.5,
    score_type: 'rrf',
    score_calibrated: false,
    source: 'redis.html',
    page: 3,
    section: 'Redis缓存实践 > 缓存穿透',
    tags: [],
    category: '后端开发',
    difficulty: '中级',
  }
  vi.mocked(api).mockImplementation(async (url) => {
    if (url === '/search') {
      return { query: '布隆过滤器', query_rewritten: null, search_type_used: 'keyword', results: [merged], total: 1, suggested_tags: [], took_ms: 30 }
    }
    if (url === '/evaluations') return {}
    return { items: [], total: 0 }
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/search', component: SearchView }] })
  await router.push('/search')
  wrapper = mount(defineComponent({ render: () => h(NMessageProvider, null, () => h(SearchView)) }), {
    global: { plugins: [router], stubs: { teleport: true, DocumentPreview: true } },
  })
  await flushPromises()
  await wrapper.get('input[aria-label="检索问题"]').setValue('布隆过滤器')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  const card = wrapper.findAll('.search-result')
  expect(card).toHaveLength(1)
  // 卡片标题一个 h2，正文里的 "## 缓存穿透" 必须被 Markdown 渲染成第二个 h2，而不是原样输出 "## "。
  expect(card[0].findAll('h2')).toHaveLength(2)
  expect(card[0].text()).toContain('缓存穿透')
  expect(card[0].findAll('mark')).toHaveLength(2)
  expect(card[0].text().split('布隆过滤器').length - 1).toBe(2)
})
