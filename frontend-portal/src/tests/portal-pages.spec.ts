import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider } from 'naive-ui'
import { http, publicHttp } from '../api/client'
import RechargeView from '../views/RechargeView.vue'
import DocsView from '../views/DocsView.vue'

function mountView(component: object) {
  const root = defineComponent({ render: () => h(NMessageProvider, null, () => h(component)) })
  return mount(root, { global: { stubs: { teleport: true } } })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('充值页的预留语义', () => {
  it('未开通渠道时给空态，且不提供任何假装能下单的按钮', async () => {
    vi.spyOn(http, 'request').mockResolvedValue({
      data: { code: 0, data: { balance_cent: 12050, transactions: [], packages: [], channels: [] } },
    })
    const wrapper = mountView(RechargeView)
    await flushPromises()
    expect(wrapper.text()).toContain('¥120.50')
    expect(wrapper.text()).toContain('支付通道尚未开通')
    expect(wrapper.text()).not.toContain('立即支付')
    expect(wrapper.findAll('button').length).toBeLessThanOrEqual(1)
    wrapper.unmount()
  })

  it('入账流水按分展示为元，不出现浮点尾数', async () => {
    vi.spyOn(http, 'request').mockResolvedValue({
      data: {
        code: 0,
        data: {
          balance_cent: 0,
          transactions: [
            {
              id: 1,
              amount_cent: 0.1 * 100,
              channel: 'manual',
              operator_id: 2,
              note: '手动入账',
              created_at: '2026-09-26T04:00:00+00:00',
            },
          ],
          packages: [],
          channels: [],
        },
      },
    })
    const wrapper = mountView(RechargeView)
    await flushPromises()
    expect(wrapper.text()).toContain('¥0.10')
    wrapper.unmount()
  })
})

describe('接口文档页', () => {
  const endpoint = {
    method: 'POST',
    path: '/v1/knowledge/search',
    summary: '四模式知识检索',
    purpose: '在已入库文档中检索分块。',
    notes: ['query 1~500 字。'],
    auth_required: true,
    request_fields: [{ name: 'query', type: 'string', required: true, default: null, description: '查询词' }],
    response_fields: [
      { field: 'search_type_used', type: 'string', meaning: '实际生效模式' },
      { field: 'took_ms', type: 'integer', meaning: '服务端耗时' },
    ],
    result_fields: [{ field: 'score_calibrated', type: 'boolean', meaning: '分数是否已校准' }],
    response_examples: { success: '{"code":0}', failure: '{"code":3001}' },
    examples: [{ language: 'curl', code: "curl -sS http://x/v1/knowledge/search" }],
    responses: ['200'],
  }

  it('渲染每个接口的响应字段表与成功失败示例', async () => {
    vi.spyOn(publicHttp, 'get').mockResolvedValue({
      status: 200,
      data: {
        code: 0,
        data: {
          base_url: 'http://x/v1/knowledge',
          version: '0.1.0',
          auth: 'Bearer',
          envelope: { success: 'ok', error: 'bad' },
          error_codes: [{ code: 3001, http: 429, meaning: '超配额' }],
          rate_limits: {
            scope: '按密钥计量',
            counters: [{ name: 'rate_limit_per_minute', default: 60, unit: '次/分钟', meaning: '令牌桶' }],
            on_exceed: { http: 429, code: 3001, header: 'Retry-After（秒）', client_rule: '退避重试' },
            shared_budget: '与离线任务共用',
            quota_note: '频控不计费',
          },
          performance: {
            measure: '单节点实测',
            throughput: [{ scenario: '缓存命中', sustainable: '60 req/s', p95: '62 ms', note: '0 错误' }],
            latency: [{ scenario: 'keyword 不带精排', value: '91 ms', note: 'took_ms 28' }],
            bottleneck: '事件循环上限',
          },
          quickstart: [{ step: 1, title: '建钥', detail: '控制台创建' }],
          sdks: [
            {
              language: 'python',
              package: 'knowforge-sdk',
              install: 'pip install knowforge-sdk',
              requires: 'Python 3.11+',
              client: 'AsyncKnowForge()',
              methods: ['search()'],
              note: '返回 data',
            },
          ],
          changelog: [{ date: '2026-09-26', changes: ['补齐响应字段表'] }],
          endpoints: [endpoint],
        },
      },
    })
    const wrapper = mountView(DocsView)
    await flushPromises()
    expect(wrapper.text()).toContain('search_type_used')
    expect(wrapper.text()).toContain('score_calibrated')
    expect(wrapper.text()).toContain('失败响应示例')
    expect(wrapper.text()).toContain('60 req/s')
    expect(wrapper.text()).toContain('快速上手')
    wrapper.unmount()
  })
})
