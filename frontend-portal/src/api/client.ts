import axios, { type AxiosRequestConfig } from 'axios'

export const http = axios.create({ baseURL: '/v1/portal', timeout: 60_000 })
export const knowledgeHttp = axios.create({ baseURL: '/v1/knowledge', timeout: 120_000 })
export const publicHttp = axios.create({ baseURL: '/v1/knowledge', timeout: 30_000 })

const TOKEN_KEY = 'knowforge.portal.token'
const PLAYGROUND_KEY = 'knowforge.portal.playgroundKey'

let accessToken = sessionStorage.getItem(TOKEN_KEY) ?? ''
let playgroundKey = sessionStorage.getItem(PLAYGROUND_KEY) ?? ''

export function setAccessToken(token: string) {
  accessToken = token
  if (token) sessionStorage.setItem(TOKEN_KEY, token)
  else sessionStorage.removeItem(TOKEN_KEY)
}

export function getAccessToken() {
  return accessToken
}

// 检索试用用的密钥只存在本标签页：服务端只保存摘要，任何时候都取不回来。
export function setPlaygroundKey(key: string) {
  playgroundKey = key.trim()
  if (playgroundKey) sessionStorage.setItem(PLAYGROUND_KEY, playgroundKey)
  else sessionStorage.removeItem(PLAYGROUND_KEY)
}

export function getPlaygroundKey() {
  return playgroundKey
}

http.interceptors.request.use((config) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      setAccessToken('')
      window.dispatchEvent(new Event('knowforge:unauthorized'))
    }
    return Promise.reject(error)
  },
)

interface Envelope<T> {
  code: number
  message: string
  data: T
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: number,
    readonly status: number,
    readonly retryAfter: string | null = null
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function unwrap<T>(promise: Promise<{ data: Envelope<T>; status: number }>): Promise<T> {
  const response = await promise
  if (response.data.code !== 0) {
    throw new ApiError(response.data.message, response.data.code, response.status)
  }
  return response.data.data
}

export function api<T>(url: string, config: AxiosRequestConfig = {}) {
  return unwrap<T>(http.request<Envelope<T>>({ url, ...config }))
}

export function publicApi<T>(url: string) {
  return unwrap<T>(publicHttp.get<Envelope<T>>(url))
}

export async function knowledgeApi<T>(path: string, key: string, config: AxiosRequestConfig = {}) {
  try {
    return await unwrap<T>(
      knowledgeHttp.request<Envelope<T>>({
        url: path,
        ...config,
        headers: { Authorization: `Bearer ${key}`, ...(config.headers ?? {}) },
      })
    )
  } catch (error) {
    // 密钥问题要带上 code 与 Retry-After 交给界面判断，不能只留一句中文消息。
    if (axios.isAxiosError(error) && error.response) {
      const envelope = error.response.data as Partial<Envelope<T>> | undefined
      throw new ApiError(
        envelope?.message ?? '请求失败',
        envelope?.code ?? 0,
        error.response.status,
        (error.response.headers['retry-after'] as string | undefined) ?? null
      )
    }
    throw error
  }
}

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const message = error.response?.data?.message
    if (typeof message === 'string') return message
    return error.code === 'ECONNABORTED' ? '请求超时，请稍后重试' : '无法连接服务，请稍后重试'
  }
  return error instanceof Error ? error.message : '操作失败'
}

const currency = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function formatCent(cent: number | null | undefined) {
  return `¥${currency.format((cent ?? 0) / 100)}`
}

export function toCent(yuan: number) {
  return Math.round(yuan * 100)
}
