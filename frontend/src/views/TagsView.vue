<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NButton, NColorPicker, NDataTable, NDrawer, NDrawerContent, NFormItem, NInput, NModal, NPagination, NSelect, NSpace, useDialog } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { api } from '../api/client'
import { useAction } from '../api/feedback'
import type { Page, TagRow } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
const { busy, run } = useAction()
const dialog = useDialog()
const rows = ref<TagRow[]>([])
const checked = ref<number[]>([])
const total = ref(0)
const page = ref(1)
const q = ref('')
const review = ref<string | null>(null)
const editOpen = ref(false)
const editing = ref<number | null>(null)
const form = reactive({ name: '', color: '#18A058', review_status: 'pending' })
const reviewOptions = [
  { label: '等待处理', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
]
const mergeOpen = ref(false)
const target = ref<number | null>(null)
const targets = ref<TagRow[]>([])
const documentsOpen = ref(false)
const documents = ref<{ doc_id: string; title: string; status: string }[]>([])
const documentsTitle = ref('')
async function fetchRows() {
  const data = await api<Page<TagRow>>('/tags', { params: { q: q.value, review_status: review.value, offset: (page.value - 1) * 30, limit: 30 } })
  rows.value = data.items; total.value = data.total
}
function load() { return run(fetchRows) }
function filter() { page.value = 1; checked.value = []; void load() }
function edit(row?: TagRow) {
  editing.value = row?.id ?? null; form.name = row?.name ?? ''; form.color = row?.color ?? '#18A058'; form.review_status = row?.review_status ?? 'pending'; editOpen.value = true
}
function save() {
  return run(async () => {
    const payload = editing.value ? form : { name: form.name, color: form.color }
    await api(editing.value ? `/tags/${editing.value}` : '/tags', { method: editing.value ? 'PATCH' : 'POST', data: payload })
    editOpen.value = false; await fetchRows()
  }, '标签已保存')
}
function batchReview(status: string) {
  return run(async () => { await api('/tags/batch-review', { method: 'POST', data: { ids: checked.value, review_status: status } }); checked.value = []; await fetchRows() }, '审核结果已保存')
}
function batchDelete() {
  dialog.warning({ title: '删除所选标签', content: '将移除标签及其文档关联，索引会异步同步。', positiveText: '删除', negativeText: '取消', onPositiveClick: () => run(async () => {
    await api('/tags/batch-delete', { method: 'POST', data: { ids: checked.value } }); checked.value = []; await fetchRows()
  }, '标签已删除') })
}
function openMerge() {
  return run(async () => {
    targets.value = (await api<Page<TagRow>>('/tags', { params: { limit: 500 } })).items
    target.value = null; mergeOpen.value = true
  })
}
function merge() {
  return run(async () => {
    await api('/tags/merge', { method: 'POST', data: { source_ids: checked.value, target_id: target.value } })
    mergeOpen.value = false; checked.value = []; await fetchRows()
  }, '标签已合并')
}
function showDocuments(row: TagRow) {
  return run(async () => {
    documents.value = (await api<{ items: typeof documents.value }>(`/tags/${row.id}/documents`)).items
    documentsTitle.value = row.name; documentsOpen.value = true
  })
}
const columns: DataTableColumns<TagRow> = [
  { type: 'selection' },
  { title: '标签名称', key: 'name', minWidth: 180, render: (row) => h(NButton, { text: true, type: 'primary', onClick: () => showDocuments(row) }, () => row.name) },
  {
    title: '颜色',
    key: 'color',
    width: 66,
    render: (row) => h('span', { style: { display: 'inline-block', width: '14px', height: '14px', borderRadius: '3px', background: row.color }, title: row.color }),
  },
  { title: '来源', key: 'auto_generated', width: 110, render: (row) => row.auto_generated ? '自动生成' : '人工创建' },
  { title: '置信度', key: 'confidence', width: 100, render: (row) => `${Math.round(row.confidence * 100)}%` },
  { title: '审核状态', key: 'review_status', width: 110, render: (row) => h(StatusTag, { status: row.review_status }) },
  { title: '关联文档', key: 'document_count', width: 100 },
  { title: '操作', key: 'action', width: 90, render: (row) => h(NButton, { size: 'small', quaternary: true, onClick: () => edit(row) }, () => '编辑') },
]
onMounted(load)
</script>

<template>
  <PageHeader title="标签管理" description="审核自动生成标签，统一知识的命名与关联。"><NButton type="primary" @click="edit()">创建标签</NButton></PageHeader>
  <div class="toolbar">
    <NInput v-model:value="q" placeholder="搜索标签" clearable @keyup.enter="filter" />
    <NSelect v-model:value="review" clearable placeholder="全部审核状态" :options="[{ label: '待审核', value: 'pending' }, { label: '已通过', value: 'approved' }, { label: '已拒绝', value: 'rejected' }]" @update:value="filter" />
    <NButton @click="filter">查询</NButton>
  </div>
  <NSpace style="margin-bottom: 20px">
    <NButton :disabled="!checked.length || busy" @click="batchReview('approved')">批量通过</NButton>
    <NButton :disabled="!checked.length || busy" @click="batchReview('rejected')">批量拒绝</NButton>
    <NButton :disabled="!checked.length || busy" @click="openMerge">合并标签</NButton>
    <NButton :disabled="!checked.length || busy" type="error" secondary @click="batchDelete">批量删除</NButton>
    <span class="muted">已选择 {{ checked.length }} 项</span>
  </NSpace>
  <NDataTable v-model:checked-row-keys="checked" :columns="columns" :data="rows" :loading="busy" :row-key="(row) => row.id" :bordered="false" />
  <div class="pagination"><NPagination v-model:page="page" :page-size="30" :item-count="total" @update:page="load" /></div>
  <NModal v-model:show="editOpen" preset="card" :title="editing ? '编辑标签' : '创建标签'" class="modal-form">
    <NFormItem label="名称"><NInput v-model:value="form.name" :maxlength="100" /></NFormItem>
    <NFormItem label="颜色"><NColorPicker v-model:value="form.color" :modes="['hex']" :show-alpha="false" /></NFormItem>
    <NFormItem v-if="editing" label="审核状态"><NSelect v-model:value="form.review_status" :options="reviewOptions" /></NFormItem>
    <div class="form-actions"><NButton @click="editOpen = false">取消</NButton><NButton type="primary" :disabled="!form.name.trim()" :loading="busy" @click="save">保存</NButton></div>
  </NModal>
  <NModal v-model:show="mergeOpen" preset="card" title="合并到目标标签" class="modal-form">
    <p class="muted">所选源标签的关联将转移到目标标签，随后删除源标签。</p>
    <NSelect v-model:value="target" filterable placeholder="选择保留的目标标签" :options="targets.map((item) => ({ label: item.name, value: item.id }))" />
    <div class="form-actions"><NButton @click="mergeOpen = false">取消</NButton><NButton type="primary" :disabled="target === null || checked.every((id) => id === target)" :loading="busy" @click="merge">确认合并</NButton></div>
  </NModal>
  <NDrawer v-model:show="documentsOpen" :width="480">
    <NDrawerContent :title="`${documentsTitle} · 关联文档`" closable>
      <div v-for="doc in documents" :key="doc.doc_id" class="section"><RouterLink :to="`/documents/${doc.doc_id}`">{{ doc.title }}</RouterLink> <StatusTag :status="doc.status" /></div>
      <p v-if="!documents.length" class="muted">暂无关联文档</p>
    </NDrawerContent>
  </NDrawer>
</template>
