<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])
const props = defineProps<{ codes: { status_code: number; count: number }[] }>()
const element = ref<HTMLDivElement | null>(null)
const description = computed(() =>
  `状态码分布：${props.codes.map((row) => `${row.status_code} 共 ${row.count} 次`).join('；')}`
)
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined

function colorOf(code: number) {
  if (code < 300) return '#18a058'
  if (code < 500) return '#f0a020'
  return '#d03050'
}

function update() {
  chart?.setOption({
    animation: false,
    grid: { left: 44, right: 16, top: 24, bottom: 30 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, renderMode: 'richText' },
    xAxis: {
      type: 'category',
      data: props.codes.map((row) => String(row.status_code)),
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
        name: '请求数',
        type: 'bar',
        barMaxWidth: 36,
        data: props.codes.map((row) => ({ value: row.count, itemStyle: { color: colorOf(row.status_code) } })),
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
watch(() => props.codes, update, { deep: true })
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
