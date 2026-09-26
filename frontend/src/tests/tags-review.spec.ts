import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NDialogProvider, NMessageProvider, NSelect } from 'naive-ui'
import TagsView from '../views/TagsView.vue'
import { api } from '../api/client'

vi.mock('../api/client', () => ({ api: vi.fn() }))
let wrapper: ReturnType<typeof mount> | undefined
afterEach(() => { wrapper?.unmount(); vi.clearAllMocks() })

const pending = {
  id: 7, name: '缓存穿透', color: '#18A058', auto_generated: true, confidence: 0.7,
  review_status: 'pending', document_count: 2,
}

async function mounted() {
  vi.mocked(api).mockResolvedValue({ items: [pending], total: 1 })
  wrapper = mount(defineComponent({ render: () => h(NMessageProvider, null, () => h(NDialogProvider, null, () => h(TagsView))) }), { global: { stubs: { teleport: true } } })
  await flushPromises()
  return wrapper
}

// The review filter also renders an NSelect, so the modal's control is the one without a placeholder.
function reviewSelect() {
  return wrapper!.findAllComponents(NSelect).find((node) => !node.props('placeholder'))
}

it('approves a renamed tag in one save so the payload sync sees the final name', async () => {
  await mounted()
  await wrapper!.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
  await flushPromises()
  expect(reviewSelect()!.props('value')).toBe('pending')
  await wrapper!.find('.modal-form input').setValue('布隆过滤器')
  reviewSelect()!.vm.$emit('update:value', 'approved')
  await flushPromises()
  await wrapper!.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/tags/7', {
    method: 'PATCH',
    data: { name: '布隆过滤器', color: '#18A058', review_status: 'approved' },
  })
})

it('keeps review status out of tag creation', async () => {
  await mounted()
  await wrapper!.findAll('button').find((button) => button.text() === '创建标签')!.trigger('click')
  await flushPromises()
  expect(reviewSelect()).toBeUndefined()
  await wrapper!.find('.modal-form input').setValue('限流')
  await wrapper!.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/tags', { method: 'POST', data: { name: '限流', color: '#18A058' } })
})
