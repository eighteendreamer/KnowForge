<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { NAlert, NButton, NSpin } from 'naive-ui'
import { downloadBlob, errorMessage, http } from '../api/client'
const props = defineProps<{ docId: string; filename: string; page?: number | null }>()
const url = ref('')
const loading = ref(false)
const error = ref('')
const blob = ref<Blob | null>(null)
let controller: AbortController | undefined
const source = computed(() => url.value && `${url.value}${blob.value?.type === 'application/pdf' ? '#page=' + (props.page ?? 1) : ''}`)
watch(() => props.docId, async (id) => {
  controller?.abort()
  const request = new AbortController()
  controller = request
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''; blob.value = null; loading.value = true; error.value = ''
  try {
    const response = await http.get<Blob>(`/documents/${encodeURIComponent(id)}/file`, { responseType: 'blob', signal: request.signal })
    if (request.signal.aborted) return
    blob.value = response.data
    url.value = URL.createObjectURL(response.data)
  } catch (reason) {
    if (!request.signal.aborted) error.value = errorMessage(reason)
  } finally { if (!request.signal.aborted) loading.value = false }
}, { immediate: true })
onUnmounted(() => { controller?.abort(); if (url.value) URL.revokeObjectURL(url.value) })
</script>

<template>
  <NSpin :show="loading">
    <NAlert v-if="error" type="error">{{ error }}</NAlert>
    <template v-if="source">
      <div class="row" style="justify-content: space-between; margin-bottom: 12px">
        <span class="muted">{{ filename }}</span>
        <NButton size="small" @click="blob && downloadBlob(blob, filename)">下载原文</NButton>
      </div>
      <iframe v-if="blob?.type === 'application/pdf'" :src="source" title="PDF 原文预览" class="preview" />
      <iframe v-else :src="source" title="HTML 原文预览" sandbox="" class="preview" />
    </template>
  </NSpin>
</template>
