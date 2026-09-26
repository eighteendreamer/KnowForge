<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NButton, NDataTable, NEmpty, NSpin, NTable } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { api } from '../api/client'
import type { DashboardData } from '../api/types'
import { useAction, formatDate } from '../api/feedback'
import PageHeader from '../components/PageHeader.vue'
import StatusTag from '../components/StatusTag.vue'
import ApiTrendChart from '../components/ApiTrendChart.vue'
const data = ref<DashboardData | null>(null)
const { busy, run } = useAction()
const metrics = [ ['documents', '文档总数'], ['chunks', '知识片段'], ['tags', '标签总数'], ['today_calls', '今日 API 调用'] ] as const
const columns: DataTableColumns<DashboardData['recent_tasks'][number]> = [
  { title: '文档', key: 'title', ellipsis: { tooltip: true } },
  { title: '状态', key: 'status', width: 110, render: (row) => h(StatusTag, { status: row.status }) },
  { title: '进度', key: 'progress', width: 90, render: (row) => `${row.progress}%` },
  { title: '更新时间', key: 'updated_at', width: 175, render: (row) => formatDate(row.updated_at) },
]
function load() { return run(async () => { data.value = await api<DashboardData>('/dashboard') }) }
onMounted(load)
</script>

<template>
  <PageHeader title="仪表盘" description="知识入库与检索服务的运行概况。">
    <NButton :loading="busy" @click="load">刷新</NButton>
    <RouterLink to="/documents"><NButton type="primary">管理文档</NButton></RouterLink>
  </PageHeader>
  <NSpin :show="busy">
    <template v-if="data">
      <div class="metric-strip">
        <div v-for="[key, label] in metrics" :key="key" class="metric">
          <span class="muted">{{ label }}</span><strong>{{ data.counts[key].toLocaleString() }}</strong>
        </div>
      </div>
      <div class="split-view">
        <section>
          <h2>近 7 天调用趋势</h2>
          <ApiTrendChart :trend="data.trend" />
        </section>
        <section>
          <h2>最近处理任务</h2>
          <NDataTable :columns="columns" :data="data.recent_tasks" :bordered="false" :row-key="(row) => row.id" />
          <div class="form-actions"><RouterLink to="/tasks">查看全部任务</RouterLink></div>
        </section>
      </div>
      <section class="section">
        <h2>热门检索</h2>
        <NEmpty v-if="!data.popular_queries.length" description="暂无检索记录" />
        <NTable v-else :bordered="false">
          <thead><tr><th>查询</th><th>调用次数</th></tr></thead>
          <tbody><tr v-for="item in data.popular_queries" :key="item.query"><td><RouterLink :to="{ path: '/search', query: { q: item.query } }">{{ item.query }}</RouterLink></td><td>{{ item.count }}</td></tr></tbody>
        </NTable>
      </section>
    </template>
  </NSpin>
</template>
