<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { NButton, NDescriptions, NDescriptionsItem, NEmpty, NFormItem, NInput, NInputNumber, NModal, NSelect, NTree, useDialog } from 'naive-ui'
import type { TreeDropInfo, TreeOption } from 'naive-ui'
import { api } from '../api/client'
import { useAction } from '../api/feedback'
import type { CategoryRow } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
const { busy, run } = useAction()
const dialog = useDialog()
const rows = ref<CategoryRow[]>([])
const tree = ref<TreeOption[]>([])
const keys = ref<number[]>([])
const selected = computed(() => rows.value.find((item) => item.id === keys.value[0]))
const open = ref(false)
const editing = ref<number | null>(null)
const form = reactive({ name: '', parent_id: null as number | null, sort_order: 0 })
const parentOptions = computed(() => {
  const excluded = new Set<number>(editing.value === null ? [] : [editing.value])
  for (const id of excluded) rows.value.filter((item) => item.parent_id === id).forEach((item) => excluded.add(item.id))
  return rows.value.filter((item) => !excluded.has(item.id)).map((item) => ({ label: item.path, value: item.id }))
})
function nodes(items: CategoryRow[]): TreeOption[] {
  return items.map((item) => ({ key: item.id, label: `${item.name}（${item.document_count}）`, children: item.children?.length ? nodes(item.children) : undefined }))
}
async function fetchRows() {
  const data = await api<{ items: CategoryRow[]; tree: CategoryRow[] }>('/categories')
  rows.value = data.items; tree.value = nodes(data.tree)
}
function load() { return run(fetchRows) }
function edit(row?: CategoryRow) {
  editing.value = row?.id ?? null
  form.name = row?.name ?? ''; form.parent_id = row?.parent_id ?? null; form.sort_order = row?.sort_order ?? 0
  open.value = true
}
function save() {
  return run(async () => {
    await api(editing.value ? `/categories/${editing.value}` : '/categories', { method: editing.value ? 'PATCH' : 'POST', data: form })
    open.value = false; await fetchRows()
  }, '分类已保存')
}
function remove() {
  const row = selected.value
  if (!row) return
  dialog.warning({ title: '删除分类', content: `确认删除“${row.name}”？有子分类或关联文档的分类不能删除。`, positiveText: '删除', negativeText: '取消', onPositiveClick: () => run(async () => {
    await api(`/categories/${row.id}`, { method: 'DELETE' }); keys.value = []; await fetchRows()
  }, '分类已删除') })
}
function drop({ node, dragNode, dropPosition }: TreeDropInfo) {
  return run(async () => {
    await api(`/categories/${dragNode.key}/move`, { method: 'POST', data: { target_id: node.key, position: dropPosition } })
    await fetchRows()
  }, '分类已移动')
}
onMounted(load)
</script>

<template>
  <PageHeader title="分类管理" description="拖拽到节点内调整父级，拖拽到节点前后调整排序。"><NButton :loading="busy" @click="load">刷新</NButton><NButton type="primary" @click="edit()">创建分类</NButton></PageHeader>
  <div class="split-view">
    <NTree
      v-model:selected-keys="keys"
      :data="tree"
      block-line
      default-expand-all
      draggable
      :disabled="busy"
      :allow-drop="() => true"
      @drop="drop"
    />
    <section v-if="selected">
      <h2>{{ selected.name }}</h2>
      <NDescriptions :column="1" label-placement="left">
        <NDescriptionsItem label="完整路径">{{ selected.path }}</NDescriptionsItem>
        <NDescriptionsItem label="文档数量">{{ selected.document_count }}</NDescriptionsItem>
        <NDescriptionsItem label="排序">{{ selected.sort_order }}</NDescriptionsItem>
      </NDescriptions>
      <div class="row" style="margin-top: 28px">
        <NButton @click="edit(selected)">编辑分类</NButton>
        <NButton @click="edit(); form.parent_id = selected.id">添加子分类</NButton>
        <NButton type="error" secondary :disabled="busy" @click="remove">删除</NButton>
      </div>
      <p class="muted">父级变更后，后代路径及关联文档索引会同步更新。</p>
    </section>
    <NEmpty v-else description="选择分类查看详情" />
  </div>
  <NModal v-model:show="open" preset="card" :title="editing ? '编辑分类' : '创建分类'" class="modal-form">
    <NFormItem label="分类名称"><NInput v-model:value="form.name" :maxlength="100" /></NFormItem>
    <NFormItem label="父级分类"><NSelect v-model:value="form.parent_id" clearable filterable placeholder="根分类" :options="parentOptions" /></NFormItem>
    <NFormItem label="排序"><NInputNumber v-model:value="form.sort_order" :min="0" :precision="0" /></NFormItem>
    <div class="form-actions"><NButton @click="open = false">取消</NButton><NButton type="primary" :disabled="!form.name.trim()" :loading="busy" @click="save">保存</NButton></div>
  </NModal>
</template>
