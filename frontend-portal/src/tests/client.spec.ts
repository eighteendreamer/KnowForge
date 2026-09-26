import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosInstance, AxiosResponse } from 'axios'
import {
  ApiError,
  getAccessToken,
  getPlaygroundKey,
  http,
  knowledgeApi,
  knowledgeHttp,
  publicApi,
  publicHttp,
  setAccessToken,
  setPlaygroundKey,
} from '../api/client'

type Stub = AxiosResponse & { config: unknown }

function respond(instance: AxiosInstance, status: number, data: unknown, headers: Record<string, string> = {}) {
  const adapter = vi.fn(async (config: unknown) => {
    const response = { data, status, statusText: '', headers, config } as unknown as Stub
    if (status >= 200 && status < 300) return response
    throw Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, config, response, code: 'ERR_BAD_RESPONSE' })
  })
  instance.defaults.adapter = adapter
  return adapter
}

describe('门户与开放接口的凭据边界', () => {
  let unauthorized: number
  const onUnauthorized = () => {
    unauthorized += 1
  }

  beforeEach(() => {
    unauthorized = 0
    window.addEventListener('knowforge:unauthorized', onUnauthorized)
    sessionStorage.clear()
    setAccessToken('')
  })

  afterEach(() => {
    window.removeEventListener('knowforge:unauthorized', onUnauthorized)
    vi.restoreAllMocks()
    delete http.defaults.adapter
    delete knowledgeHttp.defaults.adapter
    delete publicHttp.defaults.adapter
  })

  it('开放接口的 429 把 code 与 Retry-After 交给调用方', async () => {
    respond(knowledgeHttp, 429, { code: 3001, message: '超过调用频率或日配额限制' }, { 'retry-after': '12' })
    const error = await knowledgeApi('search', 'kf_test').catch((reason: unknown) => reason)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ code: 3001, status: 429, retryAfter: '12', message: '超过调用频率或日配额限制' })
  })

  it('开放接口拿到 401 不会把用户从控制台登出', async () => {
    setAccessToken('console-token')
    respond(knowledgeHttp, 401, { code: 2001, message: 'API Key 无效或已过期' })
    const error = await knowledgeApi('search', 'kf_revoked').catch((reason: unknown) => reason)
    expect(error).toBeInstanceOf(ApiError)
    expect(unauthorized).toBe(0)
    expect(getAccessToken()).toBe('console-token')
  })

  it('控制台凭据失效才派发登出事件并清掉令牌', async () => {
    setAccessToken('stale-token')
    respond(http, 401, { code: 2001, message: '登录凭据无效或已过期' })
    await expect(http.get('/auth/me')).rejects.toBeTruthy()
    expect(unauthorized).toBe(1)
    expect(getAccessToken()).toBe('')
  })

  it('检索试用的密钥只落 sessionStorage，删空即清除', () => {
    setPlaygroundKey('  kf_persisted  ')
    expect(getPlaygroundKey()).toBe('kf_persisted')
    expect(sessionStorage.getItem('knowforge.portal.playgroundKey')).toBe('kf_persisted')
    setPlaygroundKey('')
    expect(getPlaygroundKey()).toBe('')
    expect(sessionStorage.getItem('knowforge.portal.playgroundKey')).toBeNull()
  })

  it('HTTP 200 但信封 code 非零仍按失败抛出', async () => {
    respond(publicHttp, 200, { code: 1002, message: 'Query 为空或超过 500 字', data: null })
    const error = await publicApi('/api-docs').catch((reason: unknown) => reason)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe(1002)
  })
})
