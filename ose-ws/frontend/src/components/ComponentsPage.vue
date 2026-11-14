<script setup lang="ts">
import { computed, ref } from 'vue'
import { copyValue } from '../fields.mjs'
import FlowGraph from './FlowGraph.vue'
const props = defineProps<{ report: any; active?: boolean; connected?: boolean }>()
const search = ref(''), state = ref(''), selectedName = ref<string | null>(null), frozen = ref(false), held = ref<any>(null)
const sort = ref('name')
const view = (report: any) => frozen.value ? held.value : report
const selected = computed(() => view(props.report)?.components?.find((row: any) => row.name === selectedName.value))
function toggle(report: any) { if (!frozen.value) held.value = copyValue(report); frozen.value = !frozen.value }
function rows(report: any) {
  return [...(view(report)?.components || [])].filter(row => row.name.toLowerCase().includes(search.value.toLowerCase()) && (!state.value || row.state === state.value))
    .sort((a, b) => sort.value === 'name' ? a.name.localeCompare(b.name) : (b.tick_p95_ms ?? -1) - (a.tick_p95_ms ?? -1))
}
function number(value: any) { return value == null ? '—' : Number(value).toLocaleString('en', { maximumFractionDigits: 1 }) }
function queue(row: any) { return row.queues?.filter((q: any) => q.policy.buffer === 'fifo').sort((a: any, b: any) => (b.depth / b.policy.capacity) - (a.depth / a.policy.capacity))[0] }
</script>
<template>
  <div class="toolbar"><label class="search">Search components<input v-model="search" placeholder="Name contains…" /></label><label>State<select v-model="state"><option value="">All states</option><option v-for="name in ['ACTIVE','ATTENTION','LOSS/ERROR','UNAVAILABLE','STALE','WAIT INPUT','WAIT PUB','DISABLED','ENABLING','DISABLING','STARTING','IDLE']" :key="name">{{ name }}</option></select></label><label>Sort<select v-model="sort"><option value="name">Name</option><option value="tick">Callback p95</option></select></label><button @click="toggle(report)">{{ frozen ? 'Resume view' : 'Freeze view' }}</button></div>
  <p v-if="frozen" class="notice">View frozen. Server collection and captures continue.</p>
  <div class="panel table-scroll"><table><thead><tr><th>Component</th><th>State</th><th>Admissions/s</th><th>Publications/s</th><th>Callbacks/s</th><th>CPU %</th><th>RSS MiB</th><th>FIFO</th><th>Callback p95</th><th>Report age</th></tr></thead><tbody>
    <tr v-for="row in rows(report)" :key="row.name"><td><button class="text-button" @click="selectedName = row.name">{{ row.name }}</button><small class="mono muted">{{ row.generation?.slice(0, 8) || 'No generation' }}</small></td><td><span class="badge" :data-state="row.severity">{{ row.state }}</span></td><td>{{ number(row.rates.in) }}</td><td>{{ number(row.rates.out) }}</td><td>{{ number(row.rates.ticks) }}</td><td>{{ number(row.resources?.component?.cpu_percent) }}</td><td>{{ number(row.resources?.component?.rss_bytes == null ? null : row.resources.component.rss_bytes / 1048576) }}</td><td>{{ queue(row) ? `${queue(row).depth}/${queue(row).policy.capacity}` : '—' }}</td><td>{{ number(row.tick_p95_ms) }} ms</td><td>{{ number(row.report_age_ms) }} ms</td></tr>
    <tr v-if="!rows(report).length"><td colspan="10" class="empty">{{ report?.error ? 'Component data is unavailable.' : 'No matching components.' }}</td></tr>
  </tbody></table></div>
  <FlowGraph :timestamp="view(report)?.timestamp" :topology="view(report)?.topology" :components="view(report)?.components || []" :epoch="view(report)?.epoch" :error="view(report)?.error" :matching="rows(report).map(row => row.name)" :selected="selectedName" :active="active" :connected="connected" :frozen="frozen" @select="selectedName = $event" />
  <section v-if="selected" class="panel detail"><div class="section-title"><h2>{{ selected.name }}</h2><button class="quiet" @click="selectedName = null">Close details</button></div><p class="muted">Generation <code>{{ selected.generation }}</code></p><ul v-if="selected.alerts?.length" class="alerts"><li v-for="(alert, index) in selected.alerts" :key="index">{{ alert.message }}</li></ul><p v-else>No alerts in the observation window.</p><div class="table-scroll"><table><thead><tr><th>Queue</th><th>Scope</th><th>Policy</th><th>Depth</th><th>Capacity</th><th>Recent counters</th></tr></thead><tbody><tr v-for="q in selected.queues" :key="q.name + q.scope"><td>{{ q.name }}</td><td>{{ q.scope }}</td><td>{{ q.policy.buffer }}</td><td>{{ q.depth }}</td><td>{{ q.policy.capacity || '—' }}</td><td class="mono">{{ JSON.stringify(q.recent) }}</td></tr></tbody></table></div><details><summary>Cumulative counters</summary><pre>{{ JSON.stringify(selected.counters, null, 2) }}</pre></details></section>
</template>
