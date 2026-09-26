import { getAccessToken } from './client'
import type { TaskRow } from './types'

export async function readTaskStream(
  body: ReadableStream<Uint8Array>,
  onTasks: (tasks: TaskRow[]) => void,
) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) return
      buffer += decoder.decode(value, { stream: true }).replace(/\r/g, '')
      let end: number
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        const lines = block.split('\n')
        if (!lines.includes('event: tasks')) continue
        const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n')
        onTasks(JSON.parse(data) as TaskRow[])
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export async function streamTasks(signal: AbortSignal, onTasks: (tasks: TaskRow[]) => void) {
  const response = await fetch('/v1/admin/tasks/events', {
    headers: { Authorization: `Bearer ${getAccessToken()}` }, signal,
  })
  if (response.status === 401) {
    window.dispatchEvent(new Event('knowforge:unauthorized'))
    throw new Error('登录已过期')
  }
  if (!response.ok || !response.body) throw new Error('任务实时连接中断')
  await readTaskStream(response.body, onTasks)
}
