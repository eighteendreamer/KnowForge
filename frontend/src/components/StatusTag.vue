<script setup lang="ts">
import { computed } from 'vue'
import { NTag } from 'naive-ui'
const props = defineProps<{ status: string }>()
const labels: Record<string, string> = {
  pending: '等待处理', parsing: '解析中', chunking: '分块中', embedding: '向量化中',
  tagging: '标注中', indexing: '索引中', ready: '已就绪', failed: '失败', deleting: '删除中',
  running: '处理中', succeeded: '已完成', approved: '已通过', rejected: '已拒绝',
  active: '启用', disabled: '停用', revoked: '已吊销',
  evaluating: '待评估', switched: '已切换', cancelled: '已取消',
}
const type = computed(() => {
  if (['ready', 'succeeded', 'approved', 'active', 'switched'].includes(props.status)) return 'success'
  if (['failed', 'rejected', 'revoked'].includes(props.status)) return 'error'
  if (['pending', 'disabled', 'deleting', 'evaluating'].includes(props.status)) return 'warning'
  return 'info'
})
</script>

<template>
  <NTag :type="type" size="small" :bordered="false">{{ labels[status] ?? status }}</NTag>
</template>
