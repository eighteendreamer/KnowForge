import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { NMessageProvider, NUpload } from 'naive-ui'
import DocumentsView from '../views/DocumentsView.vue'
import { api } from '../api/client'

vi.mock('../api/client', () => ({ api: vi.fn(), errorMessage: (error: unknown) => String(error) }))
let wrapper: ReturnType<typeof mount> | undefined
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks() })

type CustomRequest = (options: {
  file: { name: string; file: File }
  onFinish: () => void
  onError: () => void
  onProgress: (options: { percent: number }) => void
}) => Promise<void>

// 12 MiB is two slices at the browser-side 8 MiB chunk size.
const LARGE = new File([new Uint8Array(12 * 1024 * 1024)], 'large.pdf', { type: 'application/pdf' })

it('uploads a large document in slices, then lands on its detail page', async () => {
  const calls: { url: string; data: FormData | Record<string, unknown> }[] = []
  vi.mocked(api).mockImplementation(async (url: string, config?: { data?: FormData | Record<string, unknown> }) => {
    if (url.startsWith('/documents/upload')) {
      calls.push({ url, data: config?.data ?? {} })
      return { doc_id: 'doc_new', task_id: 'task_new' }
    }
    return { items: [], total: 0 }
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/documents/:id', component: DocumentsView }, { path: '/documents', component: DocumentsView }],
  })
  await router.push('/documents')
  wrapper = mount(defineComponent({ render: () => h(NMessageProvider, null, () => h(DocumentsView)) }), {
    global: { plugins: [createPinia(), router], stubs: { teleport: true } },
  })
  await flushPromises()
  const customRequest = wrapper.findComponent(NUpload).props('customRequest') as unknown as CustomRequest
  await customRequest({ file: { name: LARGE.name, file: LARGE }, onFinish: vi.fn(), onError: vi.fn(), onProgress: vi.fn() })
  await flushPromises()
  const chunks = calls.filter((call) => call.url === '/documents/upload/chunk')
  expect(chunks.map((call) => (call.data as FormData).get('part_number'))).toEqual(['1', '2'])
  const uploadIds = new Set(chunks.map((call) => (call.data as FormData).get('upload_id')))
  expect(uploadIds.size).toBe(1)
  const [complete] = calls.filter((call) => call.url === '/documents/upload/complete')
  expect(complete.data).toMatchObject({ filename: 'large.pdf', content_type: 'application/pdf', total_parts: 2 })
  expect((complete.data as Record<string, unknown>).upload_id).toBe(uploadIds.values().next().value)
  expect(router.currentRoute.value.fullPath).toBe('/documents/doc_new')
})
