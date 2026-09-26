import { onBeforeUnmount, onMounted, ref } from 'vue'

/** 全站只用这一个断点，避免各页面各写一套宽度导致布局互相错位。 */
export const NARROW_QUERY = '(max-width: 860px)'

/**
 * 订阅一个媒体查询。用 matchMedia 而不是监听 resize：断点判断交给引擎，组件只读结果。
 * 测试里替换 window.matchMedia 即可模拟窄屏。
 */
export function useMediaQuery(query: string) {
  const matches = ref(false)
  const sync = () => {
    matches.value = window.matchMedia(query).matches
  }
  const onChange = (event: MediaQueryListEvent) => {
    matches.value = event.matches
  }
  onMounted(() => {
    sync()
    window.matchMedia(query).addEventListener('change', onChange)
  })
  onBeforeUnmount(() => {
    window.matchMedia(query).removeEventListener('change', onChange)
  })
  return matches
}

export function useNarrow() {
  return useMediaQuery(NARROW_QUERY)
}
