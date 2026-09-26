<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])
const props = defineProps<{ trend: { date: string; count: number }[] }>()
const element = ref<HTMLDivElement | null>(null)
const description = computed(() =>
  `近 7 日调用趋势：${props.trend.map((day) => `${day.date}，${day.count} 次`).join('；')}`
)
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined

function update() {
  chart?.setOption({
    animation: false,
    color: ['#18a058'],
    grid: { left: 44, right: 16, top: 24, bottom: 32 },
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    xAxis: {
      type: 'category',
      data: props.trend.map((day) => day.date.slice(5)),
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#d9d9df' } },
      axisLabel: { color: '#767c82' },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: '#767c82' },
      splitLine: { lineStyle: { color: '#efeff5' } },
    },
    series: [
      {
        name: '调用次数',
        type: 'line',
        data: props.trend.map((day) => day.count),
        symbolSize: 6,
        lineStyle: { width: 2 },
        areaStyle: { color: 'rgba(24, 160, 88, 0.08)' },
      },
    ],
  })
}

onMounted(() => {
  chart = init(element.value!)
  update()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(element.value!)
})
watch(() => props.trend, update, { deep: true })
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div ref="element" class="chart" role="img" :aria-label="description" />
</template>

<style scoped>
.chart {
  width: 100%;
  height: 240px;
}
</style>
