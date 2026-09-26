import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NDialogProvider, NMessageProvider, NSelect, NTree } from 'naive-ui'
import CategoriesView from '../views/CategoriesView.vue'
import { api } from '../api/client'

vi.mock('../api/client', () => ({ api: vi.fn() }))
let wrapper: ReturnType<typeof mount> | undefined
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks() })

it('submits all drop positions and excludes descendants from parent choices', async () => {
  const leaf = { id: 3, name: 'Leaf', path: 'Root/Child/Leaf', parent_id: 2, sort_order: 0, document_count: 0 }
  const child = { id: 2, name: 'Child', path: 'Root/Child', parent_id: 1, sort_order: 0, document_count: 0, children: [leaf] }
  const root = { id: 1, name: 'Root', path: 'Root', parent_id: null, sort_order: 0, document_count: 0, children: [child] }
  const other = { id: 4, name: 'Other', path: 'Other', parent_id: null, sort_order: 1, document_count: 0 }
  vi.mocked(api).mockResolvedValue({ items: [root, child, leaf, other], tree: [root, other] })
  wrapper = mount(defineComponent({ render: () => h(NMessageProvider, null, () => h(NDialogProvider, null, () => h(CategoriesView))) }), { global: { stubs: { teleport: true } } })
  await flushPromises()
  const tree = wrapper.findComponent(NTree)
  for (const position of ['before', 'after', 'inside'] as const) {
    tree.vm.$emit('drop', { node: { key: 4 }, dragNode: { key: 2 }, dropPosition: position })
    await flushPromises()
    expect(api).toHaveBeenCalledWith('/categories/2/move', { method: 'POST', data: { target_id: 4, position } })
  }
  tree.vm.$emit('update:selectedKeys', [2])
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === '编辑分类')!.trigger('click')
  await flushPromises()
  expect(wrapper.findComponent(NSelect).props('options')).toEqual([{ label: 'Root', value: 1 }, { label: 'Other', value: 4 }])
})
