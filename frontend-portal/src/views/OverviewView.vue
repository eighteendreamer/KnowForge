<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NAlert, NButton, NEmpty } from 'naive-ui'
import { api } from '../api/client'
import { errorMessage } from '../api/client'
import type { OverviewData } from '../api/types'
import { formatCent } from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import StatusCodeChart from '../components/StatusCodeChart.vue'
import TrendLineChart from '../components/TrendLineChart.vue'
import TypeShareChart from '../components/TypeShareChart.vue'

const data = ref<OverviewData | null>(null)
const failure = ref('')
const loading = ref(true)

async function load() {
  loading.value = true
  failure.value = ''
  try {
    data.value = await api<OverviewData>('/overview')
  } catch (error) {
    failure.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <PageHeader title="数据概览" description="调用量、延迟与检索模式分布，全部来自你自己的密钥记录。">
    <NButton size="small" :loading="loading" @click="load">刷新</NButton>
  </PageHeader>
  <NAlert v-if="failure" type="error">{{ failure }}</NAlert>
  <NEmpty v-else-if="!data && !loading" description="尚未取到数据" />
  <template v-if="data">
    <div class="metric-strip">
      <div class="metric">
        <strong>{{ formatCent(data.counts.balance_cent) }}</strong>
        <span>账户余额</span>
      </div>
      <div class="metric">
        <strong>{{ data.counts.total_calls }}</strong>
        <span>累计调用</span>
      </div>
      <div class="metric">
        <strong>{{ data.counts.today_calls }}</strong>
        <span>今日调用</span>
      </div>
      <div class="metric">
        <strong>{{ data.counts.active_keys }}</strong>
        <span>生效中的密钥</span>
      </div>
      <div class="metric">
        <strong>{{ data.counts.avg_latency_ms }} ms</strong>
        <span>平均耗时</span>
      </div>
      <div class="metric">
        <strong>{{ data.counts.p95_latency_ms }} ms</strong>
        <span>p95 耗时</span>
      </div>
    </div>
    <div class="chart-grid">
      <div class="chart-cell">
        <h3>近 7 日调用趋势</h3>
        <p class="muted">按 UTC 自然日聚合</p>
        <TrendLineChart :trend="data.trend" />
      </div>
      <div class="chart-cell">
        <h3>检索模式分布</h3>
        <p class="muted">search_type=auto 记录的是实际生效模式</p>
        <TypeShareChart :share="data.by_search_type" />
      </div>
      <div class="chart-cell">
        <h3>状态码分布</h3>
        <p class="muted">429 表示触发每分钟或每日配额</p>
        <StatusCodeChart :codes="data.by_status_code" />
      </div>
    </div>
    <section class="section">
      <h2>近 7 日热门查询</h2>
      <p v-if="!data.top_queries.length" class="muted">
        暂无记录。先<RouterLink to="/console/keys">创建一把 API Key</RouterLink>，或到
        <RouterLink to="/console/playground">检索试用</RouterLink>跑一条查询。
      </p>
      <div v-else class="table-scroll">
        <table class="table-plain">
          <thead>
            <tr><th>查询词</th><th style="width: 120px">命中次数</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in data.top_queries" :key="row.query">
              <td>{{ row.query }}</td>
              <td>{{ row.count }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
