import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, h } from 'vue'
import { createPinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'
import RegisterView from '../views/RegisterView.vue'

const RULE_MESSAGE = '2-100 位'

function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: RegisterView },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ],
  })
  const root = defineComponent({ render: () => h(NMessageProvider, null, () => h(RegisterView)) })
  return mount(root, { global: { plugins: [router, createPinia()], stubs: { teleport: true } } })
}

async function rejectsWith(wrapper: ReturnType<typeof mountView>, username: string) {
  const input = wrapper.find('input[aria-label="用户名"]')
  await input.setValue(username)
  await input.trigger('blur')
  await flushPromises()
  return wrapper.text().includes(RULE_MESSAGE)
}

beforeEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('注册页的用户名规则', () => {
  // 曾经写成 /^[\\w.@-]{2,100}$/：正则字面量里的 \\w 是"反斜杠 + w"两个字符，
  // 结果 admin1_ 这种合法用户名被判错，注册几乎点不动。规则与后端 r"^[\w.@-]+$" 同源。
  // 唯一的有意差异：JS 的 \w 只覆盖 ASCII，Python 的 \w 覆盖 Unicode，所以中文用户名前端拒、
  // 后端本可放行 —— 前端更严是安全的（不会出现"填得进去、提交才被拒"）。
  it.each(['admin1_', 'shop-assistant', 'verify_tag_ui', 'a.b@c', 'Ab9'])('接受 %s', async (username) => {
    const wrapper = mountView()
    await flushPromises()
    expect(await rejectsWith(wrapper, username)).toBe(false)
    wrapper.unmount()
  })

  it.each(['a', 'bad name!', '带空格 admin', '中文用户名'])('拒绝 %s', async (username) => {
    const wrapper = mountView()
    await flushPromises()
    expect(await rejectsWith(wrapper, username)).toBe(true)
    wrapper.unmount()
  })

  it('太长的用户名同样拒绝', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(await rejectsWith(wrapper, 'x'.repeat(101))).toBe(true)
    wrapper.unmount()
  })
})
