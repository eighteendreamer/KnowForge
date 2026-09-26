<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([PieChart, LegendComponent, TooltipComponent, CanvasRenderer])
const props = defineProps<{ share: { search_type: string; count: number }[] }>()
const element = ref<HTMLDivElement | null>(null)
const description = computed(() =>
  `检索模式分布：${props.share.map((row) => `${row.search_type} ${row.count} 次`).join('；')}`
)
let chart: EChartsType | undefined
let observer: ResizeObserver | undefined

function update() {
  chart?.setOption({
    animation: false,
    color: ['#18a058', '#36ad6a', '#73c99a', '#f0a020', '#d03050'],
    tooltip: { trigger: 'item', renderMode: 'richText' },
    legend: { bottom: 0, icon: 'circle', itemWidth: 8, textStyle: { color: '#5c6662', fontSize: 12 } },
    series: [
      {
        name: '检索模式',
        type: 'pie',
        radius: ['52%', '74%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        label: { show: false },
        data: props.share.map((row) => ({ name: row.search_type, value: row.count })),
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
watch(() => props.share, update, { deep: true })
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
