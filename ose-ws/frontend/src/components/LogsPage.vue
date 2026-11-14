<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, download } from '../api'
const props = defineProps<{ active: boolean; connected: boolean; allowed: boolean }>()
const levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL', 'FATAL']
const components = ref(''), minimum = ref('DEBUG'), search = ref(''), category = ref('')
const records = ref<any[]>([]), paused = ref(false), follow = ref(true), error = ref(''), notice = ref(''), busy = ref(false)
const viewport = ref<HTMLElement | null>(null), retained = ref(0)
const selectedLevels = computed(() => levels.slice(levels.indexOf(minimum.value)))
let cursor: string | undefined, epoch: string | undefined, revision = 0, pending = false, stopped = false
let timer: ReturnType<typeof setInterval>, debounce: ReturnType<typeof setTimeout>

async function load(force = false) {
  if (stopped || !props.active || !props.connected || !props.allowed || paused.value && !force) return
  if (busy.value) { pending = true; return }
  busy.value = true
  const version = revision
  const query = new URLSearchParams({ components: components.value.trim() || '*', levels: selectedLevels.value.join(','),
    search: search.value, logger: category.value.trim() || '*', limit: '200' })
  if (cursor !== undefined) query.set('after', cursor)
  if (epoch !== undefined) query.set('epoch', epoch)
  try {
    const result = await api('/logs?' + query)
    if (version !== revision || stopped) return
    if (epoch !== undefined && result.epoch !== epoch) records.value = []
    const seen = new Set(records.value.map(row => row.sequence))
    const combined = [...records.value, ...result.items.filter((row: any) => !seen.has(row.sequence))]
    let bytes = 0, start = combined.length
    while (start > 0 && combined.length - start < 1000) {
      const row = combined[start - 1]
      const cost = new TextEncoder().encode(row.message + row.logger + row.component).length
      if (bytes + cost > 2 * 1024 * 1024) break
      bytes += cost; start--
    }
    if (start) notice.value = 'The oldest displayed entries were removed to keep this view within its memory limit.'
    records.value = combined.slice(start)
    cursor = result.cursor; epoch = result.epoch; retained.value = result.retained
    if (result.truncated) notice.value = 'The runtime restarted or older logs expired. Some entries are unavailable.'
    error.value = ''
    if (result.more) pending = true
    if (follow.value) { await nextTick(); if (viewport.value) viewport.value.scrollTop = viewport.value.scrollHeight }
  } catch (e: any) { if (version === revision && !stopped) error.value = e.message }
  finally {
    busy.value = false
    if (pending) { pending = false; void load() }
  }
}
function reset() {
  revision++; cursor = undefined; epoch = undefined; records.value = []; notice.value = ''; error.value = ''
  clearTimeout(debounce)
  debounce = setTimeout(() => { void load(true) }, 250)
}
function togglePause() { paused.value = !paused.value; if (paused.value) revision++; else void load() }
function clearView() { records.value = []; notice.value = '' }
function time(value: string) {
  const stamp = Number(BigInt(value) / 1000000n)
  return new Date(stamp).toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', fractionalSecondDigits: 3,
  })
}
watch([components, minimum, search, category], reset)
watch(() => [props.active, props.connected, props.allowed], () => { void load() })
onMounted(() => { void load(); timer = setInterval(() => { void load() }, 1000) })
onBeforeUnmount(() => { stopped = true; clearInterval(timer); clearTimeout(debounce) })
</script>
<template>
  <p v-if="!allowed" class="notice warning">You do not have permission to read component logs.</p>
  <section v-else class="panel logs-panel">
    <div class="toolbar log-filters">
      <label>Components<input v-model="components" placeholder="All, or sensor_*,alarm" maxlength="2000" /></label>
      <label>Minimum level<select v-model="minimum"><option v-for="level in levels" :key="level">{{ level }}</option></select></label>
      <label>Category<input v-model="category" placeholder="All, or detection.*" maxlength="200" /></label>
      <label class="search">Message contains<input v-model="search" placeholder="Find text…" maxlength="500" /></label>
    </div>
    <div class="toolbar log-actions">
      <button @click="togglePause">{{ paused ? 'Resume logs' : 'Pause logs' }}</button>
      <button class="quiet" :disabled="busy || !connected" @click="load(true)">Refresh logs</button>
      <button class="quiet" :disabled="!records.length" @click="clearView">Clear view</button>
      <button class="quiet" :disabled="!records.length" @click="download(records.map(row => JSON.stringify(row)).join('\n') + '\n', 'ose-logs.jsonl', 'application/x-ndjson')">Export JSONL</button>
      <label class="inline"><input type="checkbox" v-model="follow" /> Follow latest</label>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice warning" role="status">{{ notice }}</p>
    <p class="muted small" role="status">{{ paused ? 'Display paused; runtime collection continues.' : connected ? 'Following the component log stream.' : 'Waiting for OSE. Displayed logs are kept.' }} {{ records.length }} displayed · {{ retained }} retained by OSE.</p>
    <div ref="viewport" class="table-scroll log-viewport" aria-label="Component logs" :aria-busy="busy">
      <table class="log-table"><thead><tr><th>Time</th><th>Level</th><th>Component</th><th>Category</th><th>Message</th></tr></thead>
        <tbody><tr v-for="row in records" :key="`${epoch}:${row.sequence}`" :data-level="row.level">
          <td class="mono" :title="row.time_ns + ' ns UTC'">{{ time(row.time_ns) }}<small v-if="row.simulation_time_ns != null">Simulated: {{ row.simulation_time_ns }} ns</small></td>
          <td><span class="log-level" :data-level="row.level">{{ row.level }}</span></td>
          <td class="mono" :title="`Generation ${row.generation}`">{{ row.component }}</td>
          <td class="mono">{{ row.logger || '—' }}</td><td class="log-message">{{ row.message }}</td>
        </tr><tr v-if="!records.length"><td colspan="5" class="empty">{{ busy ? 'Loading logs…' : 'No logs match these filters.' }}</td></tr></tbody>
      </table>
    </div>
    <p class="muted small log-retention">Logs are kept in memory during execution. The runtime retains up to 10,000 entries within 8 MiB; this view keeps up to 1,000 entries within 2 MiB. Older entries expire. A FATAL entry does not stop its component automatically.</p>
  </section>
</template>
