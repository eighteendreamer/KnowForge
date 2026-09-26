import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, getAccessToken, setAccessToken } from '../api/client'
import type { User } from '../api/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const token = ref(getAccessToken())
  const isSuperAdmin = computed(() => user.value?.role === 'super_admin')

  async function login(username: string, password: string) {
    const response = await api<{ access_token: string; user: User }>('/auth/login', {
      method: 'POST', data: { username, password },
    })
    setAccessToken(response.access_token)
    token.value = response.access_token
    user.value = response.user
  }

  async function restore() {
    user.value = await api<User>('/auth/me')
  }

  function logout() {
    setAccessToken('')
    token.value = ''
    user.value = null
  }

  return { user, token, isSuperAdmin, login, restore, logout }
})
