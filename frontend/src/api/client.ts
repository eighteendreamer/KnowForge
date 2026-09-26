import axios, { type AxiosRequestConfig } from 'axios'

export const http = axios.create({ baseURL: '/v1/admin', timeout: 180_000 })
let accessToken = sessionStorage.getItem('knowforge.token') ?? ''

export function setAccessToken(token: string) {
  accessToken = token
  if (token) sessionStorage.setItem('knowforge.token', token)
  else sessionStorage.removeItem('knowforge.token')
}

export function getAccessToken() { return accessToken }

http.interceptors.request.use((config) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`
  return config
})

http.interceptors.response.use((response) => response, (error: unknown) => {
  if (axios.isAxiosError(error) && error.response?.status === 401) {
    setAccessToken('')
    window.dispatchEvent(new Event('knowforge:unauthorized'))
  }
  return Promise.reject(error)
})

export async function api<T>(url: string, config: AxiosRequestConfig = {}): Promise<T> {
  const response = await http.request<{ code: number; message: string; data: T }>({ url, ...config })
  if (response.data.code !== 0) throw new Error(response.data.message)
  return response.data.data
}

const publicHttp = axios.create({ baseURL: '/v1/knowledge', timeout: 30_000 })

export async function publicApi<T>(url: string): Promise<T> {
  const response = await publicHttp.get<{ code: number; message: string; data: T }>(url)
  if (response.data.code !== 0) throw new Error(response.data.message)
  return response.data.data
}

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const message = error.response?.data?.message
    if (typeof message === 'string') return message
    return error.code === 'ECONNABORTED' ? '请求超时，请查看任务状态后再试' : '无法连接服务，请稍后重试'
  }
  return error instanceof Error ? error.message : '操作失败'
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
