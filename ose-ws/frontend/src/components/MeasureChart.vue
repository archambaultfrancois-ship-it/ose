<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer])
const props = defineProps<{ segments: any[]; title?: string }>()
const element = ref<HTMLDivElement>()
let chart: echarts.ECharts | undefined
let resize: ResizeObserver | undefined
const colors = ['#167b81', '#c47b28', '#6d63ad', '#d05766', '#4d8b52', '#3988ba']
function update() {
  chart?.setOption({
    color: colors,
    animation: false,
    tooltip: { trigger: 'axis', confine: true },
    legend: { type: 'scroll', top: 0, textStyle: { color: '#52616b', fontSize: 11 } },
    grid: { left: 65, right: 24, top: 45, bottom: 75 },
    xAxis: { type: props.segments.some(s => s.time_basis === 'simulated') ? 'value' : 'time',
      name: props.segments.some(s => s.time_basis === 'simulated') ? 'Simulated seconds' : '',
      nameLocation: 'middle', nameGap: 30,
      axisLabel: { color: '#6c7a86', ...(props.segments.some(s => s.time_basis === 'simulated') ? { formatter: (value: number) => String(value / 1000) } : {}) }, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#6c7a86' }, splitLine: { lineStyle: { color: '#e8edf0' } } },
    dataZoom: [{ type: 'inside', filterMode: 'none' }, { type: 'slider', height: 22, bottom: 10 }],
    series: props.segments.map(segment => ({
      id: segment.id, name: segment.name, type: 'line', showSymbol: false, connectNulls: false,
      lineStyle: { width: 1.8 }, data: segment.points.map((point: any) => [point.time, point.quality === 'good' ? point.value : null]),
    })),
  }, { replaceMerge: ['series'] })
}
onMounted(() => {
  if (element.value) {
    chart = echarts.init(element.value)
    resize = new ResizeObserver(() => chart?.resize())
    resize.observe(element.value)
    update()
  }
})
watch(() => props.segments, update, { deep: true })
onBeforeUnmount(() => { resize?.disconnect(); chart?.dispose() })
</script>
<template><div ref="element" class="chart" role="img" :aria-label="title || 'Observed measurement values over time. Gaps indicate unavailable data.'"></div></template>
