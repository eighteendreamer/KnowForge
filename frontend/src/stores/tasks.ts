import { defineStore } from 'pinia'
import { ref } from 'vue'
import { streamTasks } from '../api/task-stream'
import type { TaskRow } from '../api/types'

export const useTasksStore = defineStore('tasks', () => {
  const items = ref<TaskRow[]>([])
  const connected = ref(false)
  const error = ref('')
  let controller: AbortController | undefined
  let reconnect: ReturnType<typeof setTimeout> | undefined

  async function open(signal: AbortSignal) {
    try {
      await streamTasks(signal, (tasks) => {
        items.value = tasks
        connected.value = true
        error.value = ''
      })
    } catch (reason) {
      if (!signal.aborted) error.value = reason instanceof Error ? reason.message : '任务实时连接中断'
    } finally {
      connected.value = false
      if (!signal.aborted) reconnect = setTimeout(() => void open(signal), 3000)
    }
  }

  function start() {
    if (controller) return
    controller = new AbortController()
    void open(controller.signal)
  }

  function stop() {
    controller?.abort()
    controller = undefined
    clearTimeout(reconnect)
    connected.value = false
    items.value = []
  }

  return { items, connected, error, start, stop }
})
