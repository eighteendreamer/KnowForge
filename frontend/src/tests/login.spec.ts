import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { NMessageProvider } from 'naive-ui'
import LoginView from '../views/LoginView.vue'
import { http, setAccessToken } from '../api/client'

let wrapper: ReturnType<typeof mount> | undefined
afterEach(() => { wrapper?.unmount(); setAccessToken('') })

async function setup() {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/login', component: LoginView },
    { path: '/dashboard', component: defineComponent({ render: () => h('div', 'Dashboard') }) },
  ] })
  await router.push('/login')
  wrapper = mount(defineComponent({ render: () => h(NMessageProvider, null, () => h(LoginView)) }), { global: { plugins: [createPinia(), router] } })
  return router
}

describe('login form', () => {
  it('validates required fields without calling the server', async () => {
    const request = vi.spyOn(http, 'request')
    await setup()
    await wrapper!.get('form').trigger('submit')
    await flushPromises()
    expect(request).not.toHaveBeenCalled()
    expect(wrapper!.text()).toContain('请输入用户名')
  })

  it('submits actual form values and navigates after success', async () => {
    const request = vi.spyOn(http, 'request').mockResolvedValue({ data: { code: 0, data: { access_token: 'test', user: { id: 1, username: 'admin', role: 'super_admin', status: 'active' } } } })
    const router = await setup()
    await wrapper!.get('input[autocomplete="username"]').setValue('admin')
    await wrapper!.get('input[type="password"]').setValue('test-password')
    await wrapper!.get('form').trigger('submit')
    await flushPromises()
    expect(request).toHaveBeenCalledWith(expect.objectContaining({ url: '/auth/login', data: { username: 'admin', password: 'test-password' } }))
    expect(router.currentRoute.value.path).toBe('/dashboard')
  })
})
