<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { account, api, allPages, can, command, RequestError } from './api'
import consoleMark from './assets/console-mark.svg'
import ComponentsPage from './components/ComponentsPage.vue'
import LogsPage from './components/LogsPage.vue'
import MeasurementsPage from './components/MeasurementsPage.vue'
import RecordingsPage from './components/RecordingsPage.vue'
import ParametersPage from './components/ParametersPage.vue'
import { flowStream } from './flow-live'
const pages = [
  { id: 'components', label: 'Components', icon: '◫', description: 'Execution health, delivery queues and protocol timings.' },
  { id: 'logs', label: 'Logs', icon: '≡', description: 'Follow component logs and filter by source, severity, category and message.' },
  { id: 'measurements', label: 'Measurements', icon: '⌁', description: 'Follow scalar observations and inspect recent history.' },
  { id: 'recordings', label: 'Recordings', icon: '◉', description: 'Capture publications, selected inputs and internal points.' },
  { id: 'parameters', label: 'Parameters', icon: '⚙', description: 'Inspect, validate and replace typed parameter blocks.' },
]
function pageFromLocation() {
  const id = window.location.hash.slice(1)
  return pages.some(item => item.id === id) ? id : 'components'
}
function restorePage() { page.value = pageFromLocation() }
const page = ref(pageFromLocation()), token = ref(''), error = ref(''), busy = ref(false), application = ref<any>({ connected: false })
const report = ref<any>({ components: [] }), catalogue = ref<any[]>([]), history = ref<any>({ segments: [] }), selected = ref<string[]>([])
const points = ref<any[]>([]), captures = ref<any[]>([]), blocks = ref<any[]>([]), parameterChange = ref<any>(null), parameterDirty = ref(false)
const activePage = computed(() => pages.find(item => item.id === page.value)!)
const activeCaptures = computed(() => captures.value.filter(c => ['STARTING','RECORDING','STOPPING'].includes(c.state)).length)
const unresolved = ref<any[]>([]), audit = ref<any[]>([]), adminOpen = ref(false), socketConnected = ref(false)
let socket: WebSocket | null = null, refreshTimer: ReturnType<typeof setInterval>, leaseTimer: ReturnType<typeof setInterval>
let reconnect: ReturnType<typeof setTimeout> | undefined, reconnectDelay = 1000, stopped = false, loading = false
let watchId: string | undefined, windowSeconds = 900, lastHistory = 0

async function signIn() {
  busy.value = true; error.value = ''
  try { Object.assign(account, await api('/auth/login', 'POST', { token: token.value })); token.value = ''; connect(); await refresh(true) }
  catch (e: any) { error.value = e.message } finally { busy.value = false }
}
async function signOut() { try { await api('/auth/logout', 'POST') } finally { account.actor = ''; socket?.close(); watchId = undefined; selected.value = []; history.value = { segments: [] } } }
async function refresh(force = false) {
  if (!account.actor || loading) return
  loading = true
  try {
    application.value = await api('/application')
    captures.value = await allPages('/recordings')
    if (page.value === 'components') report.value = await api('/components')
    if (page.value === 'measurements') {
      catalogue.value = await allPages('/measurements')
      if (selected.value.length && (force || Date.now() - lastHistory > 1000)) {
        history.value = await api(`/series?ids=${selected.value.join(',')}&start=${Date.now() / 1000 - windowSeconds}&max_points=800`)
        lastHistory = Date.now()
      }
    }
    if (page.value === 'recordings' && (force || !points.value.length)) points.value = await allPages('/recording-points')
    if (page.value === 'parameters' && (force || !blocks.value.length)) blocks.value = await allPages('/parameters')
    error.value = ''
  } catch (e: any) { error.value = e.message } finally { loading = false }
}
async function selection(ids: string[]) {
  error.value = ''
  try {
    if (ids.length) {
      const watch = await api('/measurement-watches', 'POST', { measurement_ids: ids, ...(watchId ? { id: watchId } : {}) })
      watchId = watch.id
    } else if (watchId) { await api('/measurement-watches/' + watchId, 'DELETE'); watchId = undefined }
    selected.value = ids; subscribe(); await refresh(true)
  } catch (e: any) { error.value = e.message }
}
function subscribe() {
  if (socket?.readyState !== WebSocket.OPEN) return
  for (const id of ['application', 'page', 'parameters']) socket.send(JSON.stringify({ action: 'unsubscribe', subscription_id: id }))
  socket.send(JSON.stringify({ action: 'subscribe', subscription_id: 'application', stream: 'application', interval_ms: 1000 }))
  if (!['parameters', 'logs'].includes(page.value) && (page.value !== 'measurements' || selected.value.length)) socket.send(JSON.stringify({ action: 'subscribe', subscription_id: 'page', stream: page.value, selection: selected.value, interval_ms: 1000 }))
  if (page.value === 'parameters' && blocks.value.length) socket.send(JSON.stringify({ action: 'subscribe', subscription_id: 'parameters', stream: 'parameters', selection: blocks.value.filter(b => b.permissions.read).map(b => b.id), interval_ms: 500 }))
  subscribeFlow()
}
function subscribeFlow() {
  if (socket?.readyState !== WebSocket.OPEN) return
  socket.send(JSON.stringify({ action: 'unsubscribe', subscription_id: 'flow' }))
  if (flowStream.watchId) socket.send(JSON.stringify({ action: 'subscribe', subscription_id: 'flow', stream: 'flow', selection: [flowStream.watchId], interval_ms: flowStream.intervalMs }))
}
watch(() => [flowStream.watchId, flowStream.intervalMs], subscribeFlow)
function connect() {
  if (!account.actor || stopped || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/v1/live`)
  socket.onopen = () => { socketConnected.value = true; flowStream.connected = true; reconnectDelay = 1000; subscribe(); void refresh(true) }
  socket.onmessage = event => {
    const data = JSON.parse(event.data)
    if (data.type === 'ping') socket?.send(JSON.stringify({ action: 'pong' }))
    else if (data.type === 'parameters.changed') parameterChange.value = { ...data.data, sequence: data.sequence }
    else if (data.type === 'flow.sample') flowStream.update = data.data
    else if (data.type === 'error') error.value = data.error.message
    else if (data.type === 'resync_required') void refresh(true)
  }
  socket.onclose = event => {
    socketConnected.value = false; flowStream.connected = false; socket = null
    if (event.code === 4401) { account.actor = ''; return }
    if (account.actor && !stopped) { reconnect = setTimeout(connect, reconnectDelay + Math.random() * 500); reconnectDelay = Math.min(30000, reconnectDelay * 2) }
  }
}
watch(page, async () => {
  window.history.replaceState(window.history.state, '', '#' + page.value)
  await refresh(true); subscribe()
})
watch(() => blocks.value.map(b => b.id).join(','), subscribe)
watch(() => account.actor, actor => { if (!actor) { flowStream.connected = false; flowStream.update = null; socket?.close(); socket = null } })
async function admin() {
  adminOpen.value = !adminOpen.value
  if (adminOpen.value) { unresolved.value = await allPages('/operations'); audit.value = (await api('/audit?limit=30')).items }
}
async function closeUnknown(operation: any) {
  const note = window.prompt('Explain why this operation can be closed with an undetermined result. This does not retry it or mark it successful.')
  if (!note) return
  try { await api(`/operations/${operation.id}/close-unknown`, 'POST', { note }); unresolved.value = await allPages('/operations') } catch (e: any) { error.value = e.message }
}
function unload(event: BeforeUnloadEvent) { if (parameterDirty.value) { event.preventDefault(); event.returnValue = '' } }
onMounted(async () => {
  window.addEventListener('beforeunload', unload)
  window.addEventListener('hashchange', restorePage)
  try { Object.assign(account, await api('/session')); connect(); await refresh(true) } catch { /* Sign-in is an expected first state. */ }
  refreshTimer = setInterval(() => { void refresh() }, 1000)
  leaseTimer = setInterval(async () => {
    if (!account.actor || !selected.value.length) return
    try { const watch = await api('/measurement-watches', 'POST', { measurement_ids: selected.value, ...(watchId ? { id: watchId } : {}) }); watchId = watch.id }
    catch (e: any) { if (e.code === 'INVALID_VALUE' || e.code === 'NOT_FOUND') watchId = undefined; error.value = e.message }
  }, 20000)
})
onBeforeUnmount(() => { stopped = true; flowStream.connected = false; clearInterval(refreshTimer); clearInterval(leaseTimer); clearTimeout(reconnect); socket?.close(); window.removeEventListener('beforeunload', unload); window.removeEventListener('hashchange', restorePage) })
</script>

<template>
  <div v-if="!account.actor" class="login-shell"><section class="login-card"><div class="brand"><img class="brand-mark" :src="consoleMark" alt="" width="42" height="42" /><span>OSE <small>WEB CONSOLE</small></span></div><p class="eyebrow">APPLICATION OBSERVATORY</p><h1>A clear view of your runtime.</h1><p class="muted">Components, measurements, message captures and typed parameters in one place.</p><form @submit.prevent="signIn"><label>Local access token<input v-model="token" type="password" autocomplete="current-password" placeholder="Paste the token from your local token file" /></label><p class="muted small">Behind an authenticated proxy, leave this field empty.</p><p v-if="error" class="notice error" role="alert">{{ error }}</p><button class="primary" :disabled="busy">{{ busy ? 'Signing in…' : 'Open console →' }}</button></form><p class="small muted login-note">Connects to an application that is already running. Closing this console leaves OSE running.</p></section></div>
  <div v-else class="app-shell"><aside class="sidebar"><div class="brand"><img class="brand-mark" :src="consoleMark" alt="" width="42" height="42" /><span>OSE <small>WEB CONSOLE</small></span></div><p class="sidebar-label">OBSERVE & CONFIGURE</p><nav aria-label="Main navigation"><button v-for="item in pages" :key="item.id" :class="{ active: page === item.id }" @click="page = item.id"><span class="nav-icon">{{ item.icon }}</span><span>{{ item.label }}</span><span v-if="item.id === 'recordings' && activeCaptures" class="nav-count">{{ activeCaptures }}</span><span v-if="item.id === 'parameters' && parameterDirty" class="draft-dot" aria-label="Unsaved draft"></span></button></nav><div class="sidebar-bottom"><p class="sidebar-label">APPLICATION</p><strong>{{ application.name || 'OSE application' }}</strong><small>{{ application.mode || 'Local' }} runtime</small><span class="connection" :class="{ connected: application.connected }"><i></i>{{ application.connected ? 'Connected to OSE' : 'OSE unavailable' }}</span><button v-if="can('admin')" class="sidebar-admin" @click="admin">{{ adminOpen ? 'Close administration' : 'Operations & audit' }}</button><div class="user"><span class="avatar">{{ account.actor[0]?.toUpperCase() }}</span><span>{{ account.actor }}<small>Authenticated session</small></span><button title="Sign out" aria-label="Sign out" @click="signOut">↪</button></div></div></aside>
    <main><header class="topbar"><span><i :class="['status-dot', { online: application.connected }]"></i>{{ application.name || 'OSE application' }}<span class="separator">/</span>{{ activePage.label }}</span><div><span v-if="activeCaptures" class="recording-indicator">● {{ activeCaptures }} active captures</span><span class="live-indicator">{{ socketConnected ? 'Live channel' : 'Reconnecting live channel' }}</span></div></header><div class="content"><div class="page-heading"><div><p class="eyebrow">RUNTIME WORKSPACE</p><h1>{{ activePage.label }}</h1><p class="muted">{{ activePage.description }}</p></div><button class="quiet" :disabled="busy" @click="refresh(true)">↻ Refresh</button></div><div v-if="!application.connected" class="notice warning" role="status"><strong>OSE is unavailable.</strong> {{ application.error || 'Waiting for a connection.' }}<span v-if="application.observed_at"> Last successful observation: {{ application.observed_at }}.</span></div><p v-if="application.storage_available === false" class="notice error">Metadata storage is unavailable. New mutations are blocked; active captures can still be stopped.</p><p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <section v-if="adminOpen" class="panel administration"><div class="section-title"><h2>Uncertain operations</h2><button class="quiet" @click="adminOpen = false">Close</button></div><p class="muted">Closing an operation records an undetermined outcome. It sends no command to OSE.</p><p v-if="!unresolved.length">No unresolved operations.</p><div v-for="operation in unresolved" :key="operation.id" class="operation"><code>{{ operation.id }}</code><span>{{ operation.target }}</span><button class="quiet" @click="closeUnknown(operation)">Close as undetermined</button></div><h3>Recent audit events</h3><div class="table-scroll"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Result</th></tr></thead><tbody><tr v-for="entry in audit" :key="entry.id"><td>{{ entry.at }}</td><td>{{ entry.actor }}</td><td>{{ entry.action }}</td><td class="mono">{{ entry.target }}</td><td>{{ entry.result }}</td></tr></tbody></table></div></section>
    <div v-show="page === 'components'"><div class="stats-grid"><article class="stat"><span>Components</span><strong>{{ report.components?.length || 0 }}</strong><small>In the observed inventory</small></article><article class="stat"><span>Active</span><strong>{{ report.components?.filter((r: any) => r.state === 'ACTIVE').length || 0 }}</strong><small>Ticks or work in progress</small></article><article class="stat"><span>Needs attention</span><strong>{{ report.components?.filter((r: any) => ['warning','error'].includes(r.severity)).length || 0 }}</strong><small>Warnings and unavailable reports</small></article><article class="stat"><span>Latest status age</span><strong>{{ application.age_ms == null ? '—' : (application.age_ms / 1000).toFixed(1) }}<em v-if="application.age_ms != null">s</em></strong><small>One shared server collector</small></article></div><ComponentsPage :report="report" :active="page === 'components'" :connected="application.connected" /></div>
    <div v-show="page === 'logs'"><LogsPage :active="page === 'logs'" :connected="application.connected" :allowed="can('admin') || !!account.permissions['logs.read']?.length" /></div>
    <div v-show="page === 'measurements'"><MeasurementsPage :catalogue="catalogue" :history="history" :selected="selected" @select="selection" @window="windowSeconds = $event; refresh(true)" /></div>
    <div v-show="page === 'recordings'"><RecordingsPage :points="points" :captures="captures" @refresh="refresh(true)" /></div>
    <div v-show="page === 'parameters'"><ParametersPage :blocks="blocks" :change="parameterChange" :connected="application.connected && !application.parameter_error" @dirty="parameterDirty = $event" @refresh="refresh(true)" /></div>
    <footer>OSE Web Console <span>{{ application.server_version || '0.1.0' }}</span><span>Browser drafts and measurement history are not durable backups.</span></footer></div></main></div>
</template>
