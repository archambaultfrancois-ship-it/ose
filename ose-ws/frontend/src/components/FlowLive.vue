<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { account, api, can } from '../api'
import { flowStream } from '../flow-live'
import LiveValue from './LiveValue.vue'

const props = defineProps<{ subscription: string; epoch?: string | null; active?: boolean; connected?: boolean; frozen?: boolean }>()
const enabled = ref(false), busy = ref(false), error = ref(''), hidden = ref(document.hidden)
const intervalMs = ref(500), offset = ref(0), update = ref<any>(null), clock = ref(performance.now())
const streamState = ref('waiting')
const allowed = computed(() => can('admin') || !!account.permissions['flows.read']?.length)
const eligible = computed(() => enabled.value && allowed.value && !!account.actor && !!props.epoch && props.active !== false && props.connected !== false && !props.frozen && !hidden.value && flowStream.connected)
const age = computed(() => update.value?.sample ? Math.max(0, (update.value.age_ms || 0) + clock.value - receivedAt) / 1000 : null)
const state = computed(() => error.value ? 'Unavailable' : !enabled.value ? 'Stopped' : !eligible.value ? 'Paused' : streamState.value === 'live' ? 'Live' : 'Waiting for a publication')
let identity: string | null = null, generation = 0, receivedAt = 0, renewing = false, renewAgain = false, seenVersion: string | null = null
let renewal: ReturnType<typeof setInterval> | undefined, timer: ReturnType<typeof setInterval> | undefined

async function remove(id: string) { try { await api('/flow-watches/' + id, 'DELETE') } catch { /* The server lease also releases a lost browser. */ } }
async function detach() {
  clearInterval(renewal)
  const previous = identity; identity = null
  if (flowStream.watchId === previous) flowStream.watchId = null
  if (previous) await remove(previous)
}
function accept(data: any) {
  if (!eligible.value || data.watch_id !== identity || (data.epoch && data.epoch !== props.epoch)) return
  if (data.version != null) {
    if (seenVersion != null && BigInt(data.version) < BigInt(seenVersion)) return
    seenVersion = data.version
  }
  streamState.value = data.status
  if (data.sample) { update.value = data; receivedAt = performance.now(); clock.value = receivedAt }
  if (data.status === 'unavailable') { error.value = data.error || 'The source is unavailable.'; enabled.value = false }
}
function selection() { return { subscription: props.subscription, epoch: props.epoch, interval_ms: intervalMs.value, array_offset: offset.value } }
async function renew() {
  if (!identity || !eligible.value) return
  if (renewing) { renewAgain = true; return }
  const current = generation, id = identity
  renewing = true; renewAgain = false
  try { await api('/flow-watches', 'POST', { ...selection(), id }) }
  catch (e: any) { if (current === generation) { error.value = e.message; enabled.value = false } }
  finally { renewing = false; if (renewAgain && current === generation) { renewAgain = false; void renew() } }
}
async function reconcile() {
  const current = ++generation
  busy.value = false
  streamState.value = 'waiting'
  seenVersion = null; renewAgain = false
  await detach()
  if (current !== generation || !eligible.value) return
  busy.value = true
  try {
    const requested = selection()
    const created = await api('/flow-watches', 'POST', requested)
    if (current !== generation || !eligible.value) { await remove(created.id); return }
    identity = created.id
    flowStream.intervalMs = intervalMs.value
    flowStream.watchId = identity
    if (requested.interval_ms !== intervalMs.value || requested.array_offset !== offset.value) void renew()
    renewal = setInterval(() => { void renew() }, Math.max(1000, created.lease_s * 1000 / 3))
    const initial = await api('/flow-watches/' + identity)
    if (current === generation) accept(initial)
  } catch (e: any) {
    if (current === generation) { error.value = e.message; enabled.value = false }
  } finally { if (current === generation) busy.value = false }
}
function toggle() {
  if (enabled.value) enabled.value = false
  else { error.value = ''; update.value = null; enabled.value = true }
}
function visibility() { hidden.value = document.hidden }
watch(eligible, () => { void reconcile() })
watch(() => [props.subscription, props.epoch], () => { enabled.value = false; update.value = null; error.value = ''; void reconcile() })
watch(() => flowStream.update, data => { if (data) accept(data) })
watch([intervalMs, offset], () => { flowStream.intervalMs = intervalMs.value; void renew() })
onMounted(() => { document.addEventListener('visibilitychange', visibility); timer = setInterval(() => { clock.value = performance.now() }, 500) })
onBeforeUnmount(() => { generation++; clearInterval(timer); document.removeEventListener('visibilitychange', visibility); void detach() })
</script>
<template>
  <section class="flow-live" aria-label="Live link values">
    <div class="section-title"><div><h4>Live values</h4><p class="muted small">Latest observed source publication · read only · no history</p></div><button class="quiet" :disabled="!allowed || !epoch" :aria-pressed="enabled" @click="toggle">{{ enabled ? 'Stop Live' : 'Live' }}</button></div>
    <p v-if="!allowed" class="muted small">Live values require flows.read permission for the source topic.</p>
    <template v-else>
      <div class="live-controls"><label>Refresh<select v-model.number="intervalMs"><option :value="1000">1 update/s</option><option :value="500">2 updates/s</option><option :value="250">4 updates/s</option></select></label><label>Array start index<input type="number" v-model.number="offset" min="0" max="1000000" step="1" /></label></div>
      <p class="small live-status" role="status">{{ busy ? 'Starting Live…' : state }}<span v-if="age != null"> · last observed {{ age.toFixed(1) }} s ago</span></p>
      <p v-if="error" class="notice warning" role="alert">{{ error }}</p>
      <p v-if="enabled && !eligible" class="muted small">Live pauses while this page is hidden, the view is frozen or the connection is unavailable.</p>
      <div v-if="update?.sample" class="live-sample"><p class="muted small">Source sequence <code>{{ update.sample.source_sequence }}</code> · generation <code>{{ update.sample.generation }}</code></p><LiveValue :node="update.sample.value" :depth="0" /></div>
      <p class="muted small">Arrays show up to 16 items from the selected index. Intermediate publications may be skipped. Observation does not confirm destination delivery.</p>
    </template>
  </section>
</template>
<style scoped>
.flow-live{margin-top:20px;padding-top:16px;border-top:1px solid #cfe1df;min-width:0}.flow-live h4{margin:0 0 5px;font-size:14px}.flow-live p{margin:6px 0 10px}.flow-live .section-title{margin-bottom:10px}.flow-live button{min-height:44px}.live-controls{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.live-controls label{font-size:11px;min-width:120px}.live-controls input{width:140px}.live-status{color:#376c70;font-weight:600}.live-sample{padding:12px;background:#fff;border:1px solid #dbe7e8;border-radius:7px;margin:10px 0}.live-sample>p{overflow-wrap:anywhere}
</style>
