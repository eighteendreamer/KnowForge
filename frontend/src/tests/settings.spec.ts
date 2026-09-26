import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NDialogProvider, NInput, NMessageProvider } from 'naive-ui'
import SettingsView from '../views/SettingsView.vue'
import { api } from '../api/client'

vi.mock('../api/client', () => ({ api: vi.fn() }))
let wrapper: ReturnType<typeof mount> | undefined
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks() })

const payload = {
  configuration_id: 'c1',
  index_fingerprint: 'f'.repeat(64),
  editable_fields: ['rerank_model', 'cache_ttl_seconds', 'rerank_enabled'],
  rebuild_fields: ['embedding_model', 'embedding_dimension'],
  models_configured: true,
  rebuilds: [
    { id: 'r1', target_configuration_id: 'c2', target_collection: 'shadow_ready', status: 'evaluating', total_documents: 5, completed_documents: 5, failed_documents: 0, evaluation_id: null, error_message: null, created_at: '2026-09-26T00:23:51Z', updated_at: '2026-09-26T00:28:53Z' },
    { id: 'r2', target_configuration_id: 'c3', target_collection: 'shadow_failed', status: 'failed', total_documents: 5, completed_documents: 2, failed_documents: 1, evaluation_id: null, error_message: '重建失败：TimeoutError', created_at: '2026-09-25T09:00:00Z', updated_at: '2026-09-25T09:30:00Z' },
  ],
  evaluations: [],
}

function mounted() {
  return mount(defineComponent({ render: () => h(NMessageProvider, null, () => h(NDialogProvider, null, () => h(SettingsView))) }), { global: { stubs: { teleport: true } } })
}

it('only exposes non-index settings and submits the version it read', async () => {
  vi.mocked(api).mockResolvedValue({ ...payload, values: { rerank_model: 'rerank-4b', cache_ttl_seconds: 300, rerank_enabled: true, embedding_model: 'embed-8b', qdrant_collection: 'online' } })
  wrapper = mounted()
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/system/settings')
  expect(wrapper.text()).toContain('embed-8b')
  expect(wrapper.findAllComponents(NInput).some((input) => input.props('value') === 'embed-8b')).toBe(false)
  const save = wrapper.findAll('button').find((button) => button.text() === '保存配置')!
  expect(save.attributes('disabled')).toBeDefined()
  const rerank = wrapper.findAllComponents(NInput).find((input) => input.props('value') === 'rerank-4b')!
  rerank.vm.$emit('update:value', 'rerank-06b')
  await flushPromises()
  expect(wrapper.findAll('button').find((button) => button.text() === '保存配置')!.attributes('disabled')).toBeUndefined()
  save.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/system/settings', {
    method: 'PUT',
    data: { expected_configuration_id: 'c1', values: { rerank_model: 'rerank-06b' } },
  })
})

it('shows when a pending rebuild last moved, so a queue nobody consumes is visible', async () => {
  const stalled = {
    id: 'r3', target_configuration_id: 'c4', target_collection: 'shadow_pending', status: 'pending',
    total_documents: 31, completed_documents: 0, failed_documents: 0, evaluation_id: null, error_message: null,
    created_at: '2026-09-26T00:23:51Z', updated_at: '2026-09-26T00:23:51Z',
  }
  vi.mocked(api).mockResolvedValue({
    ...payload,
    values: { rerank_model: 'r', cache_ttl_seconds: 1, rerank_enabled: true },
    rebuilds: [stalled],
  })
  wrapper = mounted()
  await flushPromises()
  expect(wrapper.text()).toContain('最近更新')
  expect(wrapper.text()).toContain(new Date(stalled.updated_at).toLocaleString('zh-CN', { hour12: false }))
})

it('gates switching and evaluation on rebuild state', async () => {
  vi.mocked(api).mockResolvedValue({ ...payload, values: { rerank_model: 'r', cache_ttl_seconds: 1, rerank_enabled: true } })
  wrapper = mounted()
  await flushPromises()
  const buttons = wrapper.findAll('button')
  const labels = buttons.map((button) => button.text())
  const switchCells = buttons.filter((button) => button.text() === '切换')
  expect(switchCells).toHaveLength(2)
  expect(switchCells[0].attributes('disabled')).toBeUndefined()
  expect(switchCells[1].attributes('disabled')).toBeDefined()
  expect(labels.filter((label) => label === '重试')).toHaveLength(2)
  expect(wrapper.text()).toContain('重建失败：TimeoutError')
})
