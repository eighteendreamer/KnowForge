import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ApiTrendChart from '../components/ApiTrendChart.vue'

const chart = vi.hoisted(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }))
vi.mock('echarts/core', () => ({ init: vi.fn(() => chart), use: vi.fn() }))
vi.mock('echarts/charts', () => ({ LineChart: {} }))
vi.mock('echarts/components', () => ({ GridComponent: {}, TooltipComponent: {} }))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

describe('API trend chart', () => {
  it('renders server counts, updates the series and disposes on navigation', async () => {
    const wrapper = mount(ApiTrendChart, { props: { trend: [{ date: '2026-09-24', count: 0 }, { date: '2026-09-25', count: 12 }] } })
    expect(chart.setOption).toHaveBeenLastCalledWith(expect.objectContaining({ series: [expect.objectContaining({ data: [0, 12] })] }))
    expect(wrapper.attributes('aria-label')).toContain('2026-09-25，12 次')
    await wrapper.setProps({ trend: [{ date: '2026-09-25', count: 18 }] })
    expect(chart.setOption).toHaveBeenLastCalledWith(expect.objectContaining({ series: [expect.objectContaining({ data: [18] })] }))
    wrapper.unmount()
    expect(chart.dispose).toHaveBeenCalledOnce()
  })
})
