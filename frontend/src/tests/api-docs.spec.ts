import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider } from 'naive-ui'
import ApiDocsView from '../views/ApiDocsView.vue'
import { publicApi } from '../api/client'

vi.mock('../api/client', () => ({ publicApi: vi.fn(), errorMessage: (error: unknown) => String(error) }))
let wrapper: ReturnType<typeof mount> | undefined
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks() })

const field = { name: 'query', type: 'string', required: true, default: null, description: '检索问题' }
const docs = {
  base_url: 'http://127.0.0.1:8000/v1/knowledge',
  version: '0.1.0',
  auth: 'Authorization: Bearer <API Key>',
  envelope: { success: '{"code":0,...}', error: '{"code":2001,...}' },
  error_codes: [{ code: 2001, http: 401, meaning: 'API Key 无效或已撤销' }],
  endpoints: [
    {
      method: 'GET', path: '/v1/knowledge/api-docs', summary: '接口清单', purpose: '无需密钥即可读取**对外开放**的接口说明。',
      notes: ['只返回 /v1/knowledge 下的接口'], auth_required: false, request_fields: [],
      response_fields: [{ field: 'endpoints', type: 'object[]', meaning: '对外接口清单' }],
      result_fields: [], response_examples: { success: '{"code":0,"data":{"endpoints":[...]}}', failure: '{"code":1001,"message":"参数缺失或格式错误"}' },
      examples: [{ language: 'curl', code: 'curl http://host/v1/knowledge/api-docs' }], responses: ['200'],
    },
    {
      method: 'POST', path: '/v1/knowledge/search', summary: '四模式检索', purpose: '一次请求完成语义、关键词、模糊或混合检索。',
      notes: ['`top_k` 最大 50'], auth_required: true, request_fields: [field],
      response_fields: [{ field: 'search_type_used', type: 'string', meaning: '实际生效模式' }],
      result_fields: [{ field: 'score_calibrated', type: 'boolean', meaning: '分数是否已校准' }],
      response_examples: { success: '{"code":0,"message":"success","data":{"took_ms":28}}', failure: '{"code":3001,"message":"超过调用频率或日配额限制"}' },
      examples: [{ language: 'curl', code: 'curl -X POST' }, { language: 'python', code: 'client.search()' }],
      responses: ['200', '429'],
    },
  ],
}

function mounted() {
  return mount(defineComponent({ render: () => h(NMessageProvider, null, () => h(ApiDocsView)) }), {
    global: { stubs: { teleport: true } },
  })
}

it('lists open endpoints and shows purpose, fields, examples and status codes', async () => {
  vi.mocked(publicApi).mockResolvedValue(docs)
  wrapper = mounted()
  await flushPromises()
  expect(publicApi).toHaveBeenCalledWith('/api-docs')
  expect(wrapper.get('.docs-base').text()).toContain('http://127.0.0.1:8000/v1/knowledge')
  expect(wrapper.findAll('.docs-item-path').map((item) => item.text())).toEqual(['/api-docs', '/search'])
  expect(wrapper.findAll('.docs-item-summary').map((item) => item.text())).toEqual(['接口清单', '四模式检索'])
  const detail = wrapper.get('.docs-detail')
  expect(detail.get('h2').text()).toBe('GET /v1/knowledge/api-docs')
  expect(detail.get('.docs-purpose strong').text()).toBe('对外开放')
  expect(detail.text()).toContain('无需鉴权')
  expect(wrapper.text()).toContain('API Key 无效或已撤销')
  await wrapper.findAll('.docs-item')[1].trigger('click')
  await flushPromises()
  expect(wrapper.get('.docs-detail h2').text()).toBe('POST /v1/knowledge/search')
  expect(wrapper.get('.docs-detail').text()).toContain('需要 API Key')
  expect(wrapper.get('tbody td').text()).toBe('query')
  expect(wrapper.text()).toContain('search_type_used')
  expect(wrapper.text()).toContain('results[] 元素字段')
  expect(wrapper.text()).toContain('score_calibrated')
  expect(wrapper.text()).toContain('失败响应示例')
  expect(wrapper.text()).toContain('top_k 最大 50')
})

it('filters the endpoint list by path or capability', async () => {
  vi.mocked(publicApi).mockResolvedValue(docs)
  wrapper = mounted()
  await flushPromises()
  await wrapper.get('input[placeholder="按路径或能力搜索接口"]').setValue('检索')
  expect(wrapper.findAll('.docs-item')).toHaveLength(1)
  expect(wrapper.get('.docs-detail h2').text()).toBe('POST /v1/knowledge/search')
  await wrapper.get('input[placeholder="按路径或能力搜索接口"]').setValue('不存在的接口')
  expect(wrapper.findAll('.docs-item')).toHaveLength(0)
  expect(wrapper.text()).toContain('没有匹配的接口')
})

it('copies a code example through the clipboard api', async () => {
  vi.mocked(publicApi).mockResolvedValue(docs)
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  wrapper = mounted()
  await flushPromises()
  await wrapper.findAll('.docs-item')[1].trigger('click')
  await flushPromises()
  await wrapper.get('.code-copy').trigger('click')
  await flushPromises()
  expect(writeText).toHaveBeenCalledWith('curl -X POST')
  expect(wrapper.text()).toContain('示例已复制')
})
