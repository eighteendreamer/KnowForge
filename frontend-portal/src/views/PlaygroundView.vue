<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSwitch,
  NTag,
  NTreeSelect,
} from 'naive-ui'
import type { SelectOption, TreeSelectOption } from 'naive-ui'
import { ApiError, errorMessage, getPlaygroundKey, knowledgeApi, setPlaygroundKey } from '../api/client'
import type { CategoryNode, PublicTag, SearchResponse } from '../api/types'
import PageHeader from '../components/PageHeader.vue'
import MarkdownBlock from '../components/MarkdownBlock.vue'
import { useMessage } from 'naive-ui'

const message = useMessage()
const key = ref(getPlaygroundKey())
const query = ref('Redis 缓存穿透如何解决')
const loading = ref(false)
const result = ref<SearchResponse | null>(null)
const failure = ref('')
const retryAfter = ref<string | null>(null)
const form = reactive({
  search_type: 'auto' as 'auto' | 'hybrid' | 'semantic' | 'keyword' | 'fuzzy',
  top_k: 8,
  category: '',
  tags: [] as string[],
  difficulty: null as '初级' | '中级' | '高级' | null,
  rerank: true,
  query_rewrite: true,
  highlight: true,
  include_metadata: true,
  merge_adjacent: true,
})

const modeOptions = [
  { label: 'auto · 服务端按意图选路', value: 'auto' },
  { label: 'hybrid · 三路融合 + 精排', value: 'hybrid' },
  { label: 'semantic · 语义向量', value: 'semantic' },
  { label: 'keyword · BM25 关键词', value: 'keyword' },
  { label: 'fuzzy · 字面近似', value: 'fuzzy' },
]

const difficultyOptions = [
  { label: '初级', value: '初级' },
  { label: '中级', value: '中级' },
  { label: '高级', value: '高级' },
]

// 过滤项一律取自 GET /tags 与 /categories：手打的标签名或分类路径不会报错，只会静默返回空结果。
const tagOptions = ref<SelectOption[]>([])
const categoryOptions = ref<TreeSelectOption[]>([])
const approvedTagNames = ref<Set<string>>(new Set())
const filtersLoading = ref(false)
const filtersFailure = ref('')
const filtersLoaded = ref(false)

function toTreeOptions(nodes: CategoryNode[]): TreeSelectOption[] {
  return nodes.map((node) => ({
    key: node.path,
    label: `${node.path} · ${node.count} 篇公开文档`,
    children: node.children.length ? toTreeOptions(node.children) : undefined,
  }))
}

async function fetchTags(category: string) {
  const data = await knowledgeApi<{ tags: PublicTag[] }>(
    'tags',
    key.value.trim(),
    category ? { params: { category } } : {}
  )
  approvedTagNames.value = new Set(data.tags.map((row) => row.name))
  tagOptions.value = data.tags.map((row) => ({
    label: `${row.name} · ${row.count} 篇公开文档`,
    value: row.name,
  }))
  // 换到别的子树后原选标签可能一篇都圈不到，留着只会得到空结果。
  form.tags = form.tags.filter((tag) => approvedTagNames.value.has(tag))
  filtersLoaded.value = true
}

async function fetchCategories() {
  const data = await knowledgeApi<{ tree: CategoryNode[] }>('categories', key.value.trim())
  categoryOptions.value = toTreeOptions(data.tree)
}

async function loadFilterOptions() {
  if (!key.value.trim()) {
    filtersFailure.value = '粘贴 API Key 之后才能取到可选标签与分类，走的是同一条对外接口。'
    return
  }
  filtersLoading.value = true
  filtersFailure.value = ''
  try {
    await Promise.all([fetchTags(form.category), fetchCategories()])
  } catch (error) {
    filtersFailure.value = errorMessage(error)
  } finally {
    filtersLoading.value = false
  }
}

watch(
  () => form.category,
  async () => {
    if (!filtersLoaded.value || !key.value.trim()) return
    filtersLoading.value = true
    try {
      await fetchTags(form.category)
    } catch (error) {
      filtersFailure.value = errorMessage(error)
    } finally {
      filtersLoading.value = false
    }
  }
)

onMounted(() => {
  if (key.value.trim()) void loadFilterOptions()
})

const payload = computed(() => ({
  query: query.value,
  search_type: form.search_type,
  top_k: form.top_k,
  filters: {
    tags: form.tags,
    ...(form.category ? { category: form.category } : {}),
    ...(form.difficulty ? { difficulty: form.difficulty } : {}),
  },
  options: {
    rerank: form.rerank,
    query_rewrite: form.query_rewrite,
    highlight: form.highlight,
    include_metadata: form.include_metadata,
    merge_adjacent: form.merge_adjacent,
  },
}))

const activeFilters = computed(() => {
  const parts: string[] = []
  if (form.category) parts.push(`分类 ${form.category}`)
  if (form.tags.length) parts.push(`标签 ${form.tags.join('、')}`)
  if (form.difficulty) parts.push(`难度 ${form.difficulty}`)
  return parts
})

const uncalibratedScores = computed(() => result.value?.results.some((row) => !row.score_calibrated) ?? false)

const curl = computed(
  () =>
    `curl -sS ${window.location.origin}/v1/knowledge/search \\\n  -H "Authorization: Bearer $KF_KEY" \\\n` +
    `  -H 'Content-Type: application/json' \\\n  -d '${JSON.stringify(payload.value)}'`
)

function narrowByTag(tag: string) {
  if (form.tags.includes(tag)) return
  form.tags = [...form.tags, tag]
  void message.info(`已把「${tag}」加进过滤，再点一次发起检索即可收窄`)
}

async function execute() {
  setPlaygroundKey(key.value)
  if (!key.value.trim()) {
    failure.value = '请先粘贴你的 API Key。密钥明文只在创建时显示一次。'
    return
  }
  loading.value = true
  failure.value = ''
  retryAfter.value = null
  try {
    result.value = await knowledgeApi<SearchResponse>('search', key.value.trim(), {
      method: 'POST',
      data: payload.value,
    })
  } catch (error) {
    result.value = null
    if (error instanceof ApiError) {
      failure.value = `${error.message}（code ${error.code} / HTTP ${error.status}）`
      retryAfter.value = error.retryAfter
    } else {
      failure.value = errorMessage(error)
    }
  } finally {
    loading.value = false
  }
}

function copyCurl() {
  return navigator.clipboard
    .writeText(curl.value)
    .then(() => message.success('等价 curl 已复制'))
    .catch(() => message.error('浏览器拒绝了剪贴板访问'))
}
</script>

<template>
  <PageHeader title="检索试用" description="用你自己的 API Key 直接打对外接口，走的正是外部客户端的调用路径。">
    <RouterLink to="/console/keys"><NButton size="small">管理密钥</NButton></RouterLink>
  </PageHeader>
  <div class="split-view">
    <div>
      <NForm label-placement="top" @submit.prevent="execute">
        <NFormItem label="API Key">
          <div class="field">
            <NInput
              v-model:value="key"
              type="password"
              show-password-on="click"
              placeholder="kf_ 开头的密钥明文"
              :input-props="{ 'aria-label': 'API Key', autocomplete: 'off' }"
              @blur="loadFilterOptions"
            />
            <p class="field-hint">只保存在当前标签页（sessionStorage），服务端存的是摘要，任何时候都取不回来。</p>
          </div>
        </NFormItem>
        <NFormItem label="查询词">
          <NInput v-model:value="query" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" :maxlength="500" show-count />
        </NFormItem>
        <NFormItem label="检索模式">
          <NSelect v-model:value="form.search_type" :options="modeOptions" />
        </NFormItem>
        <NFormItem label="top_k">
          <NInputNumber v-model:value="form.top_k" :min="1" :max="50" :precision="0" style="width: 140px" />
        </NFormItem>
        <NFormItem label="分类（按子树展开）">
          <NTreeSelect
            v-model:value="form.category"
            :options="categoryOptions"
            :loading="filtersLoading"
            placeholder="不限（选项来自 GET /categories）"
            clearable
            filterable
          />
        </NFormItem>
        <NFormItem label="标签（只接受已过审标签）">
          <div class="field">
            <NSelect
              v-model:value="form.tags"
              :options="tagOptions"
              :loading="filtersLoading"
              multiple
              filterable
              clearable
              placeholder="不限（选项来自 GET /tags）"
            />
            <!-- 提示必须和控件放进同一个子节点：NFormItem 的插槽是 flex 横排，并列会叠在控件上。 -->
            <p class="field-hint">
              选项来自 GET /v1/knowledge/tags，服务端只返回已过审标签，按分类选择后会换成该子树内的标签。
              自己拼一个不存在的标签不会报错，只会把结果过滤成空，所以这里不给自由输入。
            </p>
            <p v-if="filtersFailure" class="field-hint">{{ filtersFailure }}</p>
          </div>
        </NFormItem>
        <NFormItem label="难度">
          <NSelect v-model:value="form.difficulty" clearable placeholder="不限" :options="difficultyOptions" />
        </NFormItem>
        <div class="option-grid">
          <label><NSwitch v-model:value="form.rerank" size="small" /> 精排 Rerank</label>
          <label><NSwitch v-model:value="form.query_rewrite" size="small" /> 查询改写</label>
          <label><NSwitch v-model:value="form.merge_adjacent" size="small" /> 合并相邻分块</label>
          <label><NSwitch v-model:value="form.highlight" size="small" /> 命中词高亮</label>
          <label><NSwitch v-model:value="form.include_metadata" size="small" /> 返回元数据</label>
        </div>
        <div class="form-actions">
          <NButton type="primary" attr-type="submit" :loading="loading">发起检索</NButton>
        </div>
      </NForm>
    </div>
    <div>
      <NAlert v-if="failure" type="error" style="margin-bottom: 18px">
        {{ failure }}
        <template v-if="retryAfter"> 请按 Retry-After 等待 {{ retryAfter }} 秒后重试，不要并发重试。</template>
      </NAlert>
      <template v-if="result">
        <div class="result-summary">
          <span>生效模式 <NTag size="small" :bordered="false" type="success">{{ result.search_type_used }}</NTag></span>
          <span>候选 {{ result.total }}</span>
          <span>返回 {{ result.results.length }}</span>
          <span>服务端 {{ result.took_ms }} ms</span>
          <span v-if="result.query_rewritten">已改写为「{{ result.query_rewritten }}」</span>
        </div>
        <NAlert v-if="!result.results.length" :type="activeFilters.length ? 'warning' : 'info'" class="result-note">
          <template v-if="activeFilters.length">
            挂着过滤条件（{{ activeFilters.join('；') }}）时没有命中，这不能证明知识库没覆盖。去掉过滤再跑一次，去掉后仍为空才是未覆盖。
          </template>
          <template v-else> 不带任何过滤条件仍为空，这才是知识库未覆盖。此时应回答“没有依据”，不要补写内容。 </template>
        </NAlert>
        <NAlert v-if="uncalibratedScores" type="warning" class="result-note">
          这些分数的 <code>score_calibrated</code> 为 false：只用于本次结果内部的相对排序，跨模式比较或套用
          0.7 这类绝对阈值都不成立。
        </NAlert>
        <div v-if="result.suggested_tags?.length" class="suggested-tags">
          <span>本次命中的已过审标签</span>
          <NTag
            v-for="tag in result.suggested_tags"
            :key="tag"
            size="small"
            type="success"
            :bordered="false"
            class="tag-chip"
            @click="narrowByTag(tag)"
          >
            {{ tag }}
          </NTag>
        </div>
        <article v-for="row in result.results" :key="row.id" class="result-item">
          <h2>{{ row.title }}</h2>
          <MarkdownBlock :source="row.content" :terms="[query]" />
          <div class="result-meta">
            <span>{{ row.source }}<template v-if="row.page !== null"> · 第 {{ row.page }} 页</template></span>
            <span v-if="row.section">章节 {{ row.section }}</span>
            <span>score {{ row.score }}（{{ row.score_type }}）</span>
            <span v-if="row.merged_ids?.length">并入 {{ row.merged_ids.length }} 个相邻分块</span>
            <span v-for="tag in row.tags ?? []" :key="tag">{{ tag }}</span>
          </div>
        </article>
      </template>
      <section class="section">
        <div class="doc-example-head">
          <h3>等价 curl</h3>
          <NButton size="tiny" tertiary @click="copyCurl">复制</NButton>
        </div>
        <pre class="code-block">{{ curl }}</pre>
      </section>
    </div>
  </div>
</template>

<style scoped>
.field {
  width: 100%;
}
.option-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 16px;
  margin: 6px 0 4px;
}
.option-grid label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #5c6662;
}
.result-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  font-size: 13px;
  color: #767c82;
  padding-bottom: 16px;
  border-bottom: 1px solid #efeff5;
}
.result-note {
  margin: 16px 0;
}
.suggested-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 16px 0;
  font-size: 13px;
  color: #767c82;
}
.tag-chip {
  cursor: pointer;
}
</style>
