import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, getAccessToken, setAccessToken } from '../api/client'
import type { Account } from '../api/types'

interface Session {
  access_token: string
  user: Account
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<Account | null>(null)
  const token = ref(getAccessToken())

  function applySession(session: Session) {
    setAccessToken(session.access_token)
    token.value = session.access_token
    user.value = session.user
  }

  async function login(username: string, password: string) {
    applySession(await api<Session>('/auth/login', { method: 'POST', data: { username, password } }))
  }

  async function register(username: string, password: string) {
    applySession(await api<Session>('/auth/register', { method: 'POST', data: { username, password } }))
  }

  async function restore() {
    user.value = await api<Account>('/auth/me')
  }

  function logout() {
    setAccessToken('')
    token.value = ''
    user.value = null
  }

  return { user, token, login, register, restore, logout }
})
