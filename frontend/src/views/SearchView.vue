<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { NAlert, NButton, NCheckbox, NDrawer, NDrawerContent, NEmpty, NFormItem, NInput, NInputNumber, NModal, NSelect, NSpace, NTag } from 'naive-ui'
import { api } from '../api/client'
import { useAction } from '../api/feedback'
import type { CategoryRow, Page, SearchResponse, SearchResult, TagRow } from '../api/types'
import DocumentPreview from '../components/DocumentPreview.vue'
import MarkdownBlock from '../components/MarkdownBlock.vue'
import PageHeader from '../components/PageHeader.vue'
const route = useRoute()
const { busy, run } = useAction()
const query = ref(typeof route.query.q === 'string' ? route.query.q : '')
const mode = ref('hybrid')
const topK = ref(8)
const category = ref<string | null>(null)
const difficulty = ref<string | null>(null)
const includeTags = ref<string[]>([])
const excludeTags = ref<string[]>([])
const options = reactive({ highlight: true, include_metadata: true, rerank: true, query_rewrite: true, language: 'zh' })
const categories = ref<CategoryRow[]>([])
const tags = ref<TagRow[]>([])
const response = ref<SearchResponse | null>(null)
const preview = ref<SearchResult | null>(null)
const annotate = ref<SearchResult | null>(null)
const judgment = ref(2)
const notes = ref('')
const tagOptions = computed(() => tags.value.map((tag) => ({ label: tag.name, value: tag.name })))
function highlightTerms(item: SearchResult): string[] {
  const entities: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', '#x27': "'" }
  return [...(item.highlight ?? '').matchAll(/<em>([\s\S]*?)<\/em>/g)]
    .map((match) => match[1].replace(/&(amp|lt|gt|quot|#x27);/g, (_, entity: string) => entities[entity]!))
    .filter((term) => term.trim().length > 0)
}
function search() {
  return run(async () => {
    response.value = await api<SearchResponse>('/search', { method: 'POST', data: {
      query: query.value, search_type: mode.value, top_k: topK.value,
      filters: { tags: includeTags.value, exclude_tags: excludeTags.value, category: category.value, difficulty: difficulty.value }, options,
    } })
  })
}
function saveJudgment() {
  return run(async () => {
    await api('/evaluations', { method: 'POST', data: { query: response.value?.query, chunk_id: annotate.value?.id, judgment: judgment.value, notes: notes.value || null } })
    annotate.value = null; notes.value = ''
  }, '人工标注已保存')
}
onMounted(() => run(async () => {
  const [categoryData, tagData] = await Promise.all([
    api<{ items: CategoryRow[] }>('/categories'), api<Page<TagRow>>('/tags', { params: { review_status: 'approved', limit: 500 } }),
  ])
  categories.value = categoryData.items; tags.value = tagData.items
}))
</script>

<template>
  <PageHeader title="检索测试" description="比较四种检索模式，追溯原文并记录人工相关性标注。" />
  <form @submit.prevent="search">
    <div class="row" style="margin-bottom: 20px">
      <NInput v-model:value="query" placeholder="输入知识问题，例如：Redis 缓存穿透如何解决？" :maxlength="500" :input-props="{ 'aria-label': '检索问题' }" size="large" />
      <NButton type="primary" attr-type="submit" size="large" :loading="busy" :disabled="!query.trim()">开始检索</NButton>
    </div>
    <div class="toolbar">
      <NSelect v-model:value="mode" :options="[{ label: '自动（按意图）', value: 'auto' }, { label: '混合检索', value: 'hybrid' }, { label: '语义检索', value: 'semantic' }, { label: '关键词检索', value: 'keyword' }, { label: '模糊检索', value: 'fuzzy' }]" />
      <NSelect v-model:value="category" clearable filterable placeholder="全部分类" :options="categories.map((item) => ({ label: item.path, value: item.path }))" />
      <NSelect v-model:value="difficulty" clearable placeholder="全部难度" :options="['初级', '中级', '高级'].map((value) => ({ label: value, value }))" />
      <NInputNumber v-model:value="topK" :min="1" :max="50" :precision="0" style="width: 120px" aria-label="返回结果数" />
    </div>
    <div class="toolbar">
      <NSelect
        v-model:value="includeTags"
        multiple
        filterable
        clearable
        placeholder="包含标签"
        :options="tagOptions"
        style="width: 280px"
      />
      <NSelect
        v-model:value="excludeTags"
        multiple
        filterable
        clearable
        placeholder="排除标签"
        :options="tagOptions"
        style="width: 280px"
      />
      <NCheckbox v-model:checked="options.rerank">Rerank 重排</NCheckbox>
      <NCheckbox v-model:checked="options.query_rewrite">查询改写</NCheckbox>
    </div>
  </form>
  <NAlert v-if="response && response.results.some((item) => !item.score_calibrated)" type="info" :show-icon="false">当前分数未校准，不代表准确率，不应直接跨模式比较或套用 0.7 阈值。</NAlert>
  <template v-if="response">
    <div class="row section"><strong>{{ response.results.length }} 条结果</strong><span class="muted">{{ response.took_ms }} ms · {{ response.search_type_used }}</span><span v-if="response.query_rewritten" class="muted">改写：{{ response.query_rewritten }}</span></div>
    <NEmpty v-if="!response.results.length" description="没有匹配的知识片段，请调整查询或过滤条件" style="margin-top: 36px" />
    <article v-for="(item, index) in response.results" :key="item.id" class="search-result">
      <div class="row" style="justify-content: space-between"><h2>{{ index + 1 }}. {{ item.title }}</h2><span class="muted">{{ item.score_type }} · {{ item.score.toFixed(4) }}</span></div>
      <p class="muted">{{ item.source }}<template v-if="item.page"> · 第 {{ item.page }} 页</template><template v-if="item.section"> · {{ item.section }}</template></p>
      <div class="search-content"><MarkdownBlock :source="item.content" :terms="highlightTerms(item)" /></div>
      <div class="row" style="justify-content: space-between">
        <NSpace><NTag v-for="tag in item.tags" :key="tag" size="small" :bordered="false">{{ tag }}</NTag><span class="muted">{{ item.category }} · {{ item.difficulty }}</span></NSpace>
        <NSpace><NButton size="small" @click="preview = item">查看原文</NButton><NButton size="small" @click="annotate = item; notes = ''; judgment = 2">标注相关性</NButton></NSpace>
      </div>
    </article>
  </template>
  <NEmpty v-else description="输入问题，验证知识库的实际检索效果" style="margin-top: 64px" />
  <NDrawer :show="preview !== null" :width="760" @update:show="(show) => { if (!show) preview = null }"><NDrawerContent title="来源追溯" closable><DocumentPreview v-if="preview" :doc-id="preview.doc_id" :filename="preview.source" :page="preview.page" /></NDrawerContent></NDrawer>
  <NModal :show="annotate !== null" preset="card" title="人工相关性标注" class="modal-form" @update:show="(show) => { if (!show) annotate = null }">
    <NFormItem label="相关性"><NSelect v-model:value="judgment" :options="[{ label: '2 — 高度相关', value: 2 }, { label: '1 — 部分相关', value: 1 }, { label: '0 — 不相关', value: 0 }]" /></NFormItem>
    <NFormItem label="备注"><NInput v-model:value="notes" type="textarea" :maxlength="2000" /></NFormItem>
    <div class="form-actions"><NButton @click="annotate = null">取消</NButton><NButton type="primary" :loading="busy" @click="saveJudgment">保存标注</NButton></div>
  </NModal>
</template>
