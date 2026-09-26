import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { getAccessToken, http, setAccessToken } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { router } from '../router'

beforeEach(() => {
  setActivePinia(createPinia())
  setAccessToken('')
})

describe('门户登录与路由守卫', () => {
  it('注册即签发门户令牌，且不会把密码留在会话存储里', async () => {
    vi.spyOn(http, 'request').mockResolvedValue({
      data: {
        code: 0,
        data: { access_token: 'portal-token', user: { id: 3, username: 'shop', role: 'end_user', status: 'active' } },
      },
    })
    const auth = useAuthStore()
    await auth.register('shop', 'a-password-nobody-guesses')
    expect(getAccessToken()).toBe('portal-token')
    expect(auth.user?.role).toBe('end_user')
    expect(JSON.stringify(sessionStorage)).not.toContain('a-password-nobody-guesses')
    auth.logout()
    expect(getAccessToken()).toBe('')
  })

  it('未登录访问控制台会被送回登录页并保留目的地', async () => {
    await router.push('/console/keys')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/console/keys')
  })

  it('欢迎页、登录与注册不需要登录态', async () => {
    for (const path of ['/', '/login', '/register']) {
      await router.push(path)
      expect(router.currentRoute.value.path).toBe(path)
    }
  })

  it('恢复登录态失败时回到登录页', async () => {
    setAccessToken('stale')
    const auth = useAuthStore()
    vi.spyOn(http, 'request').mockRejectedValue(new Error('401'))
    await expect(auth.restore()).rejects.toThrow()
  })
})
