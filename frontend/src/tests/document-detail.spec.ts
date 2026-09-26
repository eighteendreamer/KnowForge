import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { NDialogProvider, NMessageProvider, NSelect } from 'naive-ui'
import DocumentDetailView from '../views/DocumentDetailView.vue'
import { api } from '../api/client'

vi.mock('../api/client', () => ({ api: vi.fn() }))
let wrapper: ReturnType<typeof mount> | undefined
afterEach(() => wrapper?.unmount())

it('shows pending tag names separately and only submits approved selections', async () => {
  let assigned = [
    { id: 91, name: '待审缓存', review_status: 'pending' },
    { id: 92, name: 'Redis', review_status: 'approved' },
  ]
  vi.mocked(api).mockImplementation(async (url, config) => {
    if (url === '/categories') return { items: [] }
    if (url === '/tags') return { items: [{ id: 93, name: 'Java', review_status: 'approved' }], total: 1 }
    if (url.endsWith('/chunks')) return { items: [], total: 0 }
    if (url.endsWith('/tags')) {
      if (config?.method === 'PUT') { assigned = []; return {} }
      return { items: assigned }
    }
    return { doc_id: 'doc_test', title: '测试文档', source: 'test.html', status: 'ready', total_chunks: 0, is_public: true, category_id: null, upload_time: '2026-09-25T00:00:00Z' }
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/documents/:id', component: DocumentDetailView }] })
  await router.push('/documents/doc_test')
  wrapper = mount(defineComponent({ render: () => h(NMessageProvider, null, () => h(NDialogProvider, null, () => h(DocumentDetailView))) }), {
    global: { plugins: [router], stubs: { DocumentPreview: true } },
  })
  await flushPromises()
  await wrapper.findAll('.n-tabs-tab').find((tab) => tab.text() === '文档信息')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('待审缓存')
  expect(wrapper.text()).toContain('替换')
  const select = wrapper.findAllComponents(NSelect).find((component) => component.props('multiple'))!
  expect(select.props('value')).toEqual([92])
  expect(select.props('options')).toEqual(expect.arrayContaining([expect.objectContaining({ label: 'Redis', value: 92 })]))
  select.vm.$emit('update:value', [93])
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/documents/doc_test/tags', { method: 'PUT', data: { tag_ids: [93] } })
  expect(wrapper.text()).not.toContain('待审缓存')
})
