// Scenario load test for KnowForge. scripts/acceptance_load.py drives one scenario at a time
// through KNOFORGE_SCENARIO/KNOFORGE_RATE so the measured capacity of each path stays attributable.
import http from 'k6/http'
import { check } from 'k6'
import { Counter, Trend } from 'k6/metrics'

const BASE = __ENV.KNOFORGE_BASE_URL || 'http://host.docker.internal:8000'
const KEY = __ENV.KNOFORGE_API_KEY
const QUERIES = JSON.parse(__ENV.KNOFORGE_QUERIES || '["Redis 缓存穿透如何解决"]')
const modes = ['semantic', 'keyword', 'fuzzy', 'hybrid']
// 8.4 把"缓存命中"与"冷查询（不启用改写/Rerank）"定义为不调用模型的路径；只有 model 场景允许碰远程模型。
const literalModes = ['keyword', 'fuzzy']
const noModelOptions = { rerank: false, query_rewrite: false }
const scenario = __ENV.KNOFORGE_SCENARIO || 'cached'
const rate = Number(__ENV.KNOFORGE_RATE || 10)
const duration = __ENV.KNOFORGE_DURATION || '30s'
const coldVus = Number(__ENV.KNOFORGE_VUS || 2)
// 驱动脚本只把完成预热的模式放进来，未命中的请求会走到模型，不再是缓存命中路径。
const cachedModes = (__ENV.KNOFORGE_CACHED_MODES || modes.join(',')).split(',')

const errors = new Counter('knowforge_errors')
const searchLatency = new Trend('knowforge_search_ms', true)

const shared = {
  headers: { Authorization: `Bearer ${KEY}`, 'Content-Type': 'application/json' },
  timeout: '120s',
}

// 冷路径必须每次都不命中缓存，否则测的是缓存命中而不是召回。
function query(name) {
  const base = QUERIES[Math.floor(Math.random() * QUERIES.length)]
  return name === 'cold' || name === 'model' ? `${base} ${Math.random()}` : base
}

function search(list, tag, options) {
  const mode = list[Math.floor(Math.random() * list.length)]
  const body = JSON.stringify({
    query: query(tag),
    search_type: mode,
    top_k: 5,
    ...(options ? { options } : {}),
  })
  const started = Date.now()
  const response = http.post(`${BASE}/v1/knowledge/search`, body, shared)
  searchLatency.add(Date.now() - started)
  if (!check(response, { 'search 200': (r) => r.status === 200 })) errors.add(1)
}

// cached/metadata/cold 都不碰模型：cold 走 keyword+fuzzy 的字面召回，预算是本机扫描延迟。
// model 场景才启用改写与 Rerank，它的预算是远程模型延迟，不能用缓存命中的 SLO 衡量。
const budgets = { cached: 'p(95)<800', metadata: 'p(95)<800', cold: 'p(95)<3000', model: 'p(95)<90000' }
const arrival = {
  executor: 'constant-arrival-rate',
  rate,
  timeUnit: '1s',
  duration,
  preAllocatedVUs: Math.max(8, Math.ceil(rate / 2)),
  maxVUs: Math.max(16, rate * 2),
}
// 模型路径受账号配额约束，按固定 VU 压而不是按到达率，否则测的是配额等待而不是系统容量。
const vusOnly = { executor: 'constant-vus', vus: coldVus, duration }

export const options = {
  // 方案 8.4 要 P50/P95/P99，k6 默认摘要里没有 p(99)。
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(95)', 'p(99)'],
  scenarios: {
    [scenario]:
      scenario === 'model'
        ? { ...vusOnly, exec: 'model' }
        : { ...arrival, exec: scenario },
  },
  thresholds: {
    [`http_req_failed{scenario:${scenario}}`]: ['rate<0.01'],
    [`http_req_duration{scenario:${scenario}}`]: [budgets[scenario] || 'p(95)<800'],
    [`knowforge_errors{scenario:${scenario}}`]: ['count<1'],
  },
}

export function cached() {
  search(cachedModes, 'cached', noModelOptions)
}

export function cold() {
  search(literalModes, 'cold', noModelOptions)
}

export function model() {
  search(['hybrid'], 'model')
}

export function metadata() {
  const response = http.get(`${BASE}/v1/knowledge/categories`, shared)
  const second = http.get(`${BASE}/v1/knowledge/api-docs`, shared)
  if (!check(response, { 'categories 200': (r) => r.status === 200 })) errors.add(1)
  if (!check(second, { 'api-docs 200': (r) => r.status === 200 })) errors.add(1)
}

export function handleSummary(data) {
  return { stdout: `\nKNOWFORGE_SUMMARY${JSON.stringify(data)}` }
}
