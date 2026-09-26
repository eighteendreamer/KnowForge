<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NDescriptions, NDescriptionsItem, NEmpty, NFormItem, NInput, NPagination, NSelect, NSpin, NSwitch, NTabPane, NTabs, NTag, NSpace, useDialog } from 'naive-ui'
import { api } from '../api/client'
import { useAction, formatDate } from '../api/feedback'
import type { CategoryRow, ChunkRow, DocumentRow, Page, TagRow } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import DocumentPreview from '../components/DocumentPreview.vue'
import MarkdownBlock from '../components/MarkdownBlock.vue'
const route = useRoute()
const router = useRouter()
const dialog = useDialog()
const { busy, run } = useAction()
const document = ref<DocumentRow | null>(null)
const title = ref('')
const categories = ref<CategoryRow[]>([])
const tags = ref<TagRow[]>([])
const assignedTags = ref<TagRow[]>([])
const assigned = computed(() => assignedTags.value.filter((tag) => tag.review_status === 'approved').map((tag) => tag.id))
const unapprovedTags = computed(() => assignedTags.value.filter((tag) => tag.review_status !== 'approved'))
const chunks = ref<ChunkRow[]>([])
const chunkTotal = ref(0)
const chunkPage = ref(1)
const previewPage = ref<number | null>(null)
const docId = computed(() => String(route.params.id))
const categoryOptions = computed(() => categories.value.map((item) => ({ label: item.path, value: item.id })))
const tagOptions = computed(() => [...new Map([...tags.value, ...assignedTags.value.filter((tag) => tag.review_status === 'approved')].map((tag) => [tag.id, tag])).values()].map((tag) => ({ label: tag.name, value: tag.id })))
async function loadChunks() {
  const data = await api<Page<ChunkRow>>(`/documents/${docId.value}/chunks`, { params: { limit: 10, offset: (chunkPage.value - 1) * 10 } })
  chunks.value = data.items; chunkTotal.value = data.total
}
async function fetchDocument() {
  document.value = await api<DocumentRow>(`/documents/${docId.value}`)
  title.value = document.value.title
  assignedTags.value = (await api<{ items: TagRow[] }>(`/documents/${docId.value}/tags`)).items
  await loadChunks()
}
function load() { return run(fetchDocument) }
function update(values: Partial<Pick<DocumentRow, 'title' | 'category_id' | 'is_public'>>) {
  return run(async () => {
    try { document.value = await api<DocumentRow>(`/documents/${docId.value}`, { method: 'PATCH', data: values }) }
    finally { title.value = document.value?.title ?? '' }
  }, '已保存')
}
function saveTitle() { if (title.value.trim() !== document.value?.title) void update({ title: title.value.trim() }) }
function saveTags(ids: number[]) {
  return run(async () => {
    await api(`/documents/${docId.value}/tags`, { method: 'PUT', data: { tag_ids: ids } })
    assignedTags.value = (await api<{ items: TagRow[] }>(`/documents/${docId.value}/tags`)).items
  }, '标签已保存')
}
function reindex() {
  return run(async () => { await api(`/documents/${docId.value}/retry`, { method: 'POST' }); await fetchDocument() }, '处理任务已加入队列')
}
function remove() {
  dialog.warning({ title: '删除文档', content: '将删除原文、知识片段和检索索引，此操作不可恢复。', positiveText: '确认删除', negativeText: '取消', onPositiveClick: () => run(async () => {
    await api(`/documents/${docId.value}`, { method: 'DELETE' }); await router.push('/documents')
  }, '已加入删除队列') })
}
watch(docId, () => { chunkPage.value = 1; previewPage.value = null; void load() })
onMounted(() => run(async () => {
  const [categoryData, tagData] = await Promise.all([
    api<{ items: CategoryRow[] }>('/categories'), api<Page<TagRow>>('/tags', { params: { review_status: 'approved', limit: 500 } }),
  ])
  categories.value = categoryData.items; tags.value = tagData.items
  await fetchDocument()
}))
</script>

<template>
  <div class="detail-page">
    <PageHeader :title="document?.title ?? '文档详情'" description="原文、知识片段与人工修订。">
      <NButton @click="router.push('/documents')">返回列表</NButton>
      <NButton :loading="busy" @click="load">刷新</NButton>
      <NButton v-if="document && ['failed', 'ready'].includes(document.status)" :disabled="busy" @click="reindex">{{ document.status === 'failed' ? '重试处理' : '重新索引' }}</NButton>
      <NButton type="error" secondary :disabled="busy || document?.status === 'deleting'" @click="remove">删除</NButton>
    </PageHeader>
    <NSpin :show="busy">
      <template v-if="document">
        <NAlert v-if="document.parse_error" type="error" style="margin-bottom: 20px">{{ document.parse_error }}</NAlert>
        <NDescriptions :column="4" label-placement="left" style="margin-bottom: 24px">
          <NDescriptionsItem label="状态"><StatusTag :status="document.status" /></NDescriptionsItem>
          <NDescriptionsItem label="页数">{{ document.total_pages ?? '—' }}</NDescriptionsItem>
          <NDescriptionsItem label="片段">{{ document.total_chunks }}</NDescriptionsItem>
          <NDescriptionsItem label="上传时间">{{ formatDate(document.upload_time) }}</NDescriptionsItem>
        </NDescriptions>
        <div class="detail-grid">
          <section class="detail-left"><DocumentPreview :doc-id="docId" :filename="document.source" :page="previewPage" /></section>
          <section class="detail-right">
            <NTabs type="line" animated>
              <NTabPane name="chunks" tab="知识片段">
                <NEmpty v-if="!chunks.length" description="文档就绪后显示知识片段" />
                <article v-for="chunk in chunks" :key="chunk.chunk_id" class="chunk">
                  <div class="chunk-meta"><span>#{{ chunk.chunk_index + 1 }}</span><span>{{ chunk.token_count }} tokens</span><span>{{ chunk.difficulty }}</span><NButton v-if="chunk.page_start" text type="primary" @click="previewPage = chunk.page_start">第 {{ chunk.page_start }}{{ chunk.page_end !== chunk.page_start ? `–${chunk.page_end}` : '' }} 页</NButton></div>
                  <strong>{{ chunk.section_path.join(' / ') }}</strong>
                  <MarkdownBlock :source="chunk.text" />
                </article>
                <div class="pagination"><NPagination v-model:page="chunkPage" :page-size="10" :item-count="chunkTotal" @update:page="run(loadChunks)" /></div>
              </NTabPane>
              <NTabPane name="metadata" tab="文档信息">
                <NFormItem label="标题（失焦自动保存）"><NInput v-model:value="title" :disabled="busy" :maxlength="500" @blur="saveTitle" @keyup.enter="saveTitle" /></NFormItem>
                <NFormItem label="人工分类">
                  <NSelect
                    :value="document.category_id"
                    :options="categoryOptions"
                    :disabled="busy"
                    clearable
                    filterable
                    placeholder="恢复自动分类"
                    @update:value="(value) => update({ category_id: value })"
                  />
                </NFormItem>
                <NFormItem label="公开检索"><NSwitch :value="document.is_public" :disabled="busy" @update:value="(value) => update({ is_public: value })" /><span class="muted" style="margin-left: 12px">关闭后仅管理员可检索</span></NFormItem>
                <NFormItem v-if="unapprovedTags.length" label="未通过审核的关联标签">
                  <NSpace><NTag v-for="tag in unapprovedTags" :key="tag.id" :type="tag.review_status === 'pending' ? 'warning' : 'error'" size="small">{{ tag.name }} · {{ tag.review_status === 'pending' ? '待审核' : '已拒绝' }}</NTag></NSpace>
                </NFormItem>
                <NAlert v-if="unapprovedTags.length" type="warning" style="margin-bottom: 16px">修改人工标签会替换文档及片段的全部标签，以上未审核通过的标签将被移除；也可先到标签管理完成审核。</NAlert>
                <NFormItem label="人工标签（仅可选已审核标签）">
                  <NSelect
                    :value="assigned"
                    :options="tagOptions"
                    :disabled="busy"
                    multiple
                    filterable
                    clearable
                    @update:value="saveTags"
                  />
                </NFormItem>
                <p class="muted">信息变更自动保存。分类和标签变更会同步到检索索引。</p>
              </NTabPane>
            </NTabs>
          </section>
        </div>
      </template>
    </NSpin>
  </div>
</template>
