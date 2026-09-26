import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import { errorMessage } from './client'

export function useAction() {
  const busy = ref(false)
  const message = useMessage()
  async function run(action: () => Promise<void>, success?: string) {
    if (busy.value) return
    busy.value = true
    try {
      await action()
      if (success) message.success(success)
    } catch (error) {
      message.error(errorMessage(error))
    } finally {
      busy.value = false
    }
  }
  return { busy, run }
}

export function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

const currency = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function formatCent(cent: number | null | undefined) {
  return `¥${currency.format((cent ?? 0) / 100)}`
}

export function toCent(yuan: number) {
  return Math.round(yuan * 100)
}
