import { describe, expect, it, vi } from 'vitest'
import { readTaskStream, streamTasks } from '../api/task-stream'
import { setAccessToken } from '../api/client'

function stream(parts: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({ start(controller) { parts.forEach((part) => controller.enqueue(part)); controller.close() } })
}

describe('task SSE parser', () => {
  it('handles split UTF-8, multiple frames, CRLF and heartbeat comments', async () => {
    const text = ': heartbeat\r\n\r\nevent: tasks\r\ndata: [{"title":"中文任务","status":"running"}]\r\n\r\nevent: tasks\ndata: []\n\n'
    const bytes = new TextEncoder().encode(text)
    const callback = vi.fn()
    await readTaskStream(stream(Array.from(bytes, (byte) => new Uint8Array([byte]))), callback)
    expect(callback.mock.calls).toEqual([[[{ title: '中文任务', status: 'running' }]], [[]]])
  })

  it('uses bearer authentication and terminates unauthorized streams', async () => {
    setAccessToken('test-only-token')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 401 }))
    const unauthorized = vi.fn()
    window.addEventListener('knowforge:unauthorized', unauthorized)
    await expect(streamTasks(new AbortController().signal, vi.fn())).rejects.toThrow('登录已过期')
    expect(fetchMock.mock.calls[0][1]?.headers).toEqual({ Authorization: 'Bearer test-only-token' })
    expect(unauthorized).toHaveBeenCalledOnce()
    window.removeEventListener('knowforge:unauthorized', unauthorized)
    setAccessToken('')
  })
})
