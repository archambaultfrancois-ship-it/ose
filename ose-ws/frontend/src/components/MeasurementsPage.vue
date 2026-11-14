<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import MeasureChart from './MeasureChart.vue'
import { api } from '../api'
import { copyValue, makeTree } from '../fields.mjs'
import PathTree from './PathTree.vue'
const props = defineProps<{ catalogue: any[]; history: any; selected: string[]; simulated?: boolean }>()
const emit = defineEmits<{ select: [ids: string[]]; window: [seconds: number] }>()
const search = ref(''), frozen = ref(false), split = ref(false), seconds = ref(900), held = ref<any>(null)
const groups = computed(() => {
  const groups: Record<string, any[]> = {}
  for (const item of props.catalogue.filter(item => item.name.toLowerCase().includes(search.value.toLowerCase()))) (groups[item.component] ||= []).push(item)
  return groups
})
const segments = computed(() => (frozen.value ? held.value : props.history)?.segments || [])
function select(id: string, checked: boolean) { emit('select', checked ? [...props.selected, id] : props.selected.filter(i => i !== id)) }
function freeze() { if (!frozen.value) held.value = copyValue(props.history); frozen.value = !frozen.value }
watch(seconds, value => emit('window', value))
</script>
<template>
  <div class="workspace two-column">
    <aside class="panel catalogue"><div class="section-title"><h2>Measurement catalogue</h2><span class="count">{{ catalogue.length }}</span></div><label class="search">Find a measurement<input v-model="search" placeholder="Component or dotted name…" /></label>
      <details v-for="(items, component) in groups" :key="component" open><summary class="catalogue-group">{{ component }} <span class="muted">{{ items.length }}</span></summary><PathTree :nodes="makeTree(items, 'local_name', '.')" mode="measurements" :checked="selected" @check="select" /></details><p v-if="!catalogue.length" class="empty">No measurements have been discovered.</p>
    </aside>
    <section>
      <p v-if="simulated" class="notice">The horizontal axis and time window use simulated time. Points are collected snapshots; intermediate emissions may be missed.</p>
      <div class="toolbar"><label>Time window<select v-model="seconds"><option :value="60">Last minute</option><option :value="300">Last 5 minutes</option><option :value="900">Last 15 minutes</option></select></label><label class="inline"><input type="checkbox" v-model="split" /> Separate charts</label><button @click="freeze">{{ frozen ? 'Resume view' : 'Freeze view' }}</button><a v-if="selected.length" class="button quiet" :href="`/api/v1/series/export?ids=${selected.join(',')}`">Export CSV</a></div>
      <div v-if="!selected.length" class="panel empty large"><div class="empty-symbol">↗</div><h2>Follow a measurement</h2><p>Select signals from the catalogue to begin keeping observations.</p><p class="muted">History starts when you select a signal. Intermediate emissions may be missed.</p></div>
      <template v-else><div v-if="history?.truncated" class="notice">The history window was shortened by retention or buffer limits.</div><div v-if="split"><article v-for="id in selected" :key="id" class="panel chart-panel"><h2>{{ catalogue.find(item => item.id === id)?.name }}</h2><MeasureChart :segments="segments.filter((s: any) => s.measurement_id === id)" /></article></div><article v-else class="panel chart-panel"><MeasureChart :segments="segments" /></article><p class="muted small">{{ frozen ? 'View frozen. ' : '' }}Points are observations of latest values, not every emission. Generations and missing reports remain separate. {{ history?.buffer_samples || 0 }} buffered observations.</p></template>
    </section>
  </div>
</template>
