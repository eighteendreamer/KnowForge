import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { http, getAccessToken, setAccessToken } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { router } from '../router'

beforeEach(() => { setActivePinia(createPinia()); setAccessToken('') })

describe('admin authentication', () => {
  it('keeps only the access token in session storage and clears it on logout', async () => {
    vi.spyOn(http, 'request').mockResolvedValue({ data: { code: 0, data: { access_token: 'token', user: { id: 1, username: 'admin', role: 'super_admin', status: 'active' } } } })
    const auth = useAuthStore()
    await auth.login('admin', 'private-password')
    expect(auth.isSuperAdmin).toBe(true)
    expect(getAccessToken()).toBe('token')
    expect(JSON.stringify(sessionStorage)).not.toContain('private-password')
    auth.logout()
    expect(getAccessToken()).toBe('')
    expect(auth.user).toBeNull()
  })

  it('blocks protected pages without a token and preserves the destination', async () => {
    await router.push('/documents')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/documents')
  })

  it('blocks content administrators from super-admin screens', async () => {
    const auth = useAuthStore()
    auth.token = 'token'
    auth.user = { id: 2, username: 'editor', role: 'content_admin', status: 'active' }
    await router.push('/api-keys')
    expect(router.currentRoute.value.path).toBe('/dashboard')
  })
})
