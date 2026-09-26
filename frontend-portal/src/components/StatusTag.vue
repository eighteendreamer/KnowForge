<script setup lang="ts">
import { computed } from 'vue'
import { NTag } from 'naive-ui'
const props = defineProps<{ status: string }>()
const labels: Record<string, string> = {
  active: '启用',
  disabled: '停用',
  revoked: '已吊销',
}
const type = computed(() => {
  if (props.status === 'active') return 'success'
  if (props.status === 'revoked') return 'error'
  return 'warning'
})
</script>

<template>
  <NTag :type="type" size="small" :bordered="false">{{ labels[status] ?? status }}</NTag>
</template>
