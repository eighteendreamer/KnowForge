<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { NButton, NDataTable, NInput, NSelect, NPagination, NUpload, NUploadDragger, NModal, NFormItem, NIcon, useMessage } from 'naive-ui'
import { CloudUploadOutline } from '@vicons/ionicons5'
import type { DataTableColumns, UploadCustomRequestOptions } from 'naive-ui'
import { api, errorMessage } from '../api/client'
import { useAction, formatDate } from '../api/feedback'
import type { CategoryRow, DocumentRow, Page } from '../api/types'
import { useTasksStore } from '../stores/tasks'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
const router = useRouter()
const tasks = useTasksStore()
const message = useMessage()
const { busy, run } = useAction()
const rows = ref<DocumentRow[]>([])
const total = ref(0)
const page = ref(1)
const q = ref('')
const status = ref<string | null>(null)
const category = ref<number | null>(null)
const categories = ref<CategoryRow[]>([])
const checked = ref<string[]>([])
const moveOpen = ref(false)
const pendingUploads = ref(0)
let lastUploaded = ''
const moveCategory = ref<number | null>(null)
const categoryOptions = computed(() => categories.value.map((item) => ({ label: item.path, value: item.id })))
const statusOptions = [ ['pending', '等待处理'], ['parsing', '解析中'], ['chunking', '分块中'], ['embedding', '向量化中'], ['tagging', '标注中'], ['indexing', '索引中'], ['ready', '已就绪'], ['failed', '失败'], ['deleting', '删除中'] ].map(([value, label]) => ({ value, label }))
const columns: DataTableColumns<DocumentRow> = [
  { type: 'selection', disabled: (row) => row.status === 'deleting' },
  { title: '文档名称', key: 'title', minWidth: 250, render: (row) => h(RouterLink, { to: `/documents/${row.doc_id}` }, () => row.title) },
  {
    title: '标签',
    key: 'tags',
    width: 190,
    ellipsis: { tooltip: true },
    render: (row) => (row.tags.length ? row.tags.slice(0, 3).join('、') + (row.tag_total > 3 ? ` 等 ${row.tag_total} 个` : '') : '—'),
  },
  { title: '来源', key: 'source', minWidth: 170, ellipsis: { tooltip: true }, render: (row) => row.source },
  { title: '分类', key: 'category_id', width: 170, ellipsis: { tooltip: true }, render: (row) => categories.value.find((item) => item.id === row.category_id)?.path ?? '未分类' },
  { title: '状态', key: 'status', width: 100, render: (row) => h(StatusTag, { status: row.status }) },
  { title: '页数 / 片段', key: 'total_chunks', width: 110, render: (row) => `${row.total_pages ?? '—'} / ${row.total_chunks}` },
  { title: '可见性', key: 'is_public', width: 85, render: (row) => row.is_public ? '公开' : '内部' },
  { title: '上传时间', key: 'upload_time', width: 180, render: (row) => formatDate(row.upload_time) },
  { title: '操作', key: 'actions', width: 80, render: (row) => h(NButton, { size: 'small', quaternary: true, onClick: () => router.push(`/documents/${row.doc_id}`) }, () => '详情') },
]
async function fetchRows() {
  const data = await api<Page<DocumentRow>>('/documents', { params: { q: q.value, status: status.value, category_id: category.value, limit: 20, offset: (page.value - 1) * 20 } })
  rows.value = data.items
  total.value = data.total
}
function load() { return run(fetchRows) }
function filter() { page.value = 1; checked.value = []; void load() }
// Plan 4.3 step 3: slices keep a large upload retryable and stay below the model-free request cap.
const CHUNK_BYTES = 8 * 1024 * 1024
async function upload({ file, onFinish, onError, onProgress }: UploadCustomRequestOptions) {
  if (!file.file) return
  if (!/\.(pdf|html?)$/i.test(file.name)) {
    message.error('仅支持 PDF 或 HTML 文件'); onError(); return
  }
  pendingUploads.value += 1
  const uploadId = crypto.randomUUID()
  const total = Math.max(1, Math.ceil(file.file.size / CHUNK_BYTES))
  try {
    for (let index = 0; index < total; index += 1) {
      const part = new FormData()
      part.append('upload_id', uploadId)
      part.append('part_number', String(index + 1))
      part.append('file', file.file.slice(index * CHUNK_BYTES, (index + 1) * CHUNK_BYTES), file.name)
      await api('/documents/upload/chunk', { method: 'POST', data: part })
      onProgress({ percent: ((index + 1) / total) * 99 })
    }
    const created = await api<{ doc_id: string }>('/documents/upload/complete', {
      method: 'POST',
      data: {
        upload_id: uploadId,
        filename: file.name,
        content_type: file.file.type,
        total_parts: total,
        category_id: category.value,
      },
    })
    lastUploaded = created.doc_id
    onFinish(); message.success(`${file.name} 已加入处理队列`); await load()
  } catch (error) {
    onError(); message.error(errorMessage(error))
  }
  pendingUploads.value -= 1
  // Plan 4.3 step 7: land on the document detail page once the whole dropped batch is queued.
  if (pendingUploads.value === 0 && lastUploaded) void router.push(`/documents/${lastUploaded}`)
}
function moveDocuments() {
  return run(async () => {
    await api('/categories/move-documents', { method: 'POST', data: { doc_ids: checked.value, category_id: moveCategory.value } })
    moveOpen.value = false; checked.value = []; await fetchRows()
  }, '分类已更新，索引同步已加入队列')
}
let refresh: ReturnType<typeof setTimeout> | undefined
watch(() => tasks.items.map((item) => `${item.id}:${item.updated_at}`).join('|'), () => {
  clearTimeout(refresh)
  refresh = setTimeout(() => void load(), 300)
})
onUnmounted(() => clearTimeout(refresh))
onMounted(() => run(async () => {
  const data = await api<{ items: CategoryRow[] }>('/categories')
  categories.value = data.items
  await fetchRows()
}))
</script>

<template>
  <PageHeader title="文档管理" :description="`共 ${total} 份文档 · 上传后自动解析、标注与建立索引。`">
    <NButton :loading="busy" @click="load">刷新</NButton>
    <NButton :disabled="!checked.length" @click="moveOpen = true">移动分类（{{ checked.length }}）</NButton>
  </PageHeader>
  <NUpload multiple accept=".pdf,.html,.htm" :custom-request="upload" :max="20" class="upload-zone">
    <NUploadDragger>
      <div class="row" style="justify-content: center">
        <NIcon :size="26" color="#18a058"><CloudUploadOutline /></NIcon>
        <span>点击或拖拽文档到此处上传</span><span class="muted">PDF / HTML · 8 MiB 分片上传，上限取系统设置的上传大小限制</span>
      </div>
    </NUploadDragger>
  </NUpload>
  <div class="toolbar" style="margin-top: 24px">
    <NInput v-model:value="q" clearable placeholder="搜索文档名称" :input-props="{ 'aria-label': '搜索文档名称' }" @keyup.enter="filter" />
    <NSelect v-model:value="status" :options="statusOptions" clearable placeholder="全部状态" @update:value="filter" />
    <NSelect v-model:value="category" :options="categoryOptions" clearable filterable placeholder="全部分类" @update:value="filter" />
    <NButton @click="filter">查询</NButton>
  </div>
  <NDataTable
    v-model:checked-row-keys="checked"
    :loading="busy"
    :columns="columns"
    :data="rows"
    :row-key="(row) => row.doc_id"
    :bordered="false"
    :scroll-x="1080"
  />
  <div class="pagination"><NPagination v-model:page="page" :page-size="20" :item-count="total" @update:page="load" /></div>
  <NModal v-model:show="moveOpen" preset="card" title="移动文档分类" class="modal-form">
    <NFormItem label="目标分类"><NSelect v-model:value="moveCategory" :options="categoryOptions" filterable clearable placeholder="清除人工分类，恢复自动分类" /></NFormItem>
    <div class="form-actions"><NButton @click="moveOpen = false">取消</NButton><NButton type="primary" :loading="busy" @click="moveDocuments">确认移动</NButton></div>
  </NModal>
</template>
