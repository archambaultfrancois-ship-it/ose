<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { account, api, command, download } from '../api'
import { changes, copyValue, makeTree } from '../fields.mjs'
import PathTree from './PathTree.vue'
import SchemaField from './SchemaField.vue'
import SchemaView from './SchemaView.vue'
const props = defineProps<{ blocks: any[]; change: any; connected: boolean }>()
const emit = defineEmits<{ dirty: [value: boolean]; refresh: [] }>()
const search = ref(''), filter = ref(''), selected = ref<any>(null), schema = ref<any>(null), base = ref<any>(null), draft = ref<any>(null), latest = ref<any>(null)
const preview = ref<any>(null), showContract = ref(false), error = ref(''), message = ref(''), busy = ref(false), stale = ref(false)
const importPlan = ref<any>(null), exportIds = ref<string[]>([])
const importInput = ref<HTMLInputElement | null>(null)
const validation = ref<{ pointer: string; message: string } | null>(null)
const editor = ref<HTMLElement | null>(null), errorSummary = ref<HTMLElement | null>(null)
let rejectedValue: string | undefined
function valueAt(pointer: string) {
  return pointer === '' ? draft.value : pointer.slice(1).split('/').reduce((value, key) => value?.[key.replaceAll('~1', '/').replaceAll('~0', '~')], draft.value)
}
const differences = computed(() => changes(base.value, draft.value))
const dirty = computed(() => differences.value.length > 0)
const remoteChanged = computed(() => latest.value && JSON.stringify(latest.value.value) !== JSON.stringify(base.value))
const visible = computed(() => props.blocks.filter(block => block.path.toLowerCase().includes(search.value.toLowerCase()) && (!filter.value || (filter.value === 'write' ? block.permissions.write : !block.permissions.write))))
const selectable = computed(() => visible.value.filter(block => block.permissions.read))
watch(dirty, value => emit('dirty', value))
watch(draft, () => {
  if (preview.value) message.value = ''
  preview.value = null
  if (validation.value && JSON.stringify(valueAt(validation.value.pointer)) !== rejectedValue) { validation.value = null; error.value = '' }
}, { deep: true })
watch(() => props.connected, connected => { if (!connected && selected.value) stale.value = true })
watch(() => props.change, async change => {
  if (change?.block_id === selected.value?.id) {
    try {
      latest.value = await api(`/parameters/${selected.value.id}`)
      stale.value = false
      if (!dirty.value) { base.value = copyValue(latest.value.value); draft.value = copyValue(latest.value.value) }
    } catch (e: any) { error.value = e.message; stale.value = true }
  }
})
async function revealError() {
  if (validation.value) validation.value = { ...validation.value }
  await nextTick()
  const field = validation.value ? Array.from(editor.value?.querySelectorAll<HTMLElement>('[data-pointer]') || []).find(node => node.dataset.pointer === validation.value?.pointer) : null
  let parent = field?.parentElement
  while (parent && parent !== editor.value) { if (parent.tagName === 'DETAILS') (parent as HTMLDetailsElement).open = true; parent = parent.parentElement }
  if (field) for (const details of Array.from(field.querySelectorAll('details'))) details.open = true
  const target = field?.matches('input,select,textarea') ? field : field?.querySelector<HTMLElement>('[aria-invalid="true"]:not([data-optional-presence])') || field?.querySelector<HTMLElement>('[aria-invalid="true"],:scope > input,:scope > select,:scope > textarea,:scope > details > summary') || errorSummary.value
  ;(field || target)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  target?.focus({ preventScroll: true })
}
async function act(effect: () => Promise<any>, locateField = false) {
  busy.value = true; error.value = ''; message.value = ''; validation.value = null
  let failed = false
  try { await effect() }
  catch (e: any) {
    error.value = e.message; failed = true
    if (locateField && typeof e.details?.pointer === 'string') {
      validation.value = { pointer: e.details.pointer, message: e.message }
      rejectedValue = JSON.stringify(valueAt(e.details.pointer))
    }
    if (locateField) preview.value = null
  } finally { busy.value = false }
  if (failed && locateField) await revealError()
}
async function select(block: any) {
  if (busy.value) return
  if (dirty.value && !window.confirm('Discard the current draft and open another block? Cancel to keep editing.')) return
  await act(async () => {
    const [contract, value] = await Promise.all([api(`/parameters/${block.id}/schema`), api(`/parameters/${block.id}`)])
    selected.value = block; schema.value = contract; base.value = copyValue(value.value); draft.value = copyValue(value.value); latest.value = value; preview.value = null; stale.value = false
  })
}
async function reload() {
  if (dirty.value && !window.confirm('Discard local changes and reload the current remote value?')) return
  if (selected.value) { draft.value = base.value; await select(selected.value) }
}
function discard() { draft.value = copyValue(base.value); validation.value = null; error.value = '' }
async function apply() {
  if (busy.value || stale.value || !props.connected || !selected.value?.permissions.write) return
  await act(async () => {
    const validated = await api(`/parameters/${selected.value.id}/validate`, 'POST', { value: copyValue(draft.value), schema_digest: schema.value.schema_digest })
    preview.value = validated
    const result = await command(`/parameters/${selected.value.id}`, 'PUT', validated)
    if (result?.result === 'stored') {
      base.value = copyValue(validated.value); draft.value = copyValue(validated.value); preview.value = null
      latest.value = await api(`/parameters/${selected.value.id}`)
      message.value = 'Stored by OSE. Components may not have consumed this value yet.'
    }
  }, true)
}
async function exportValues() {
  await act(async () => { const ids = exportIds.value.length ? exportIds.value : selected.value ? [selected.value.id] : []; const text = await api('/parameter-exports', 'POST', { block_ids: ids }, false, true); download(text, 'parameters.json') })
}
async function importFile(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]
  if (!file) return
  await act(async () => {
    if (file.size > 16 * 1024 * 1024) throw new Error('The import exceeds 16 MiB.')
    // Send text directly. JSON.parse would round OSE integer literals before upload.
    const response = await fetch('/api/v1/parameter-imports/preview', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': account.csrf }, body: await file.text(), credentials: 'same-origin' })
    const result = await response.json()
    if (!response.ok) throw new Error(result.error?.message || 'Import validation failed.')
    importPlan.value = result
  })
  input.value = ''
}
</script>
<template>
  <p v-if="error && !selected" class="notice error" role="alert">{{ error }}</p><p v-if="message" class="notice" role="status">{{ message }}</p>
  <div class="toolbar"><button class="quiet" :disabled="busy || (!exportIds.length && !selected)" @click="exportValues">Export selected blocks</button><button type="button" class="quiet" :disabled="busy" @click="importInput?.click()">Import OSE JSON</button><input ref="importInput" type="file" accept="application/json,.json" hidden @change="importFile" /><span class="muted small">Exports preserve exact 64-bit integers. Changes remain volatile in OSE.</span></div>
  <section v-if="importPlan" class="panel detail"><div class="section-title"><h2>Import preview</h2><button class="quiet" @click="importPlan = null">Discard plan</button></div><p class="notice warning">Each block replaces its complete value. Writes are sequential and stop at the first error; there is no transaction across blocks. This plan expires after 10 minutes.</p><details v-for="block in importPlan.blocks" :key="block.block_id"><summary>{{ block.path }} · {{ changes(block.before, block.value).length }} changed fields</summary><pre>{{ JSON.stringify(changes(block.before, block.value), null, 2) }}</pre></details><button class="primary" :disabled="busy || !connected" @click="act(async () => { const result = await command(`/parameter-imports/${importPlan.id}/apply`); message = JSON.stringify(result); importPlan = null; emit('refresh') })">Apply complete blocks</button></section>
  <div class="workspace two-column"><aside class="panel catalogue"><div class="section-title"><h2>Parameter blocks</h2><span class="count">{{ blocks.length }}</span></div><label>Find a block<input v-model="search" placeholder="Path contains…" /></label><label>Access<select v-model="filter"><option value="">All blocks</option><option value="write">Writable by web policy</option><option value="read">Read only by web policy</option></select></label><div class="toolbar" role="group" aria-label="Block selection"><button type="button" class="quiet small" :disabled="busy || !selectable.length" title="Select all visible readable blocks" @click="exportIds = selectable.map(block => block.id)">All</button><button type="button" class="quiet small" :disabled="busy || !exportIds.length" title="Clear block selection" @click="exportIds = []">None</button><span class="muted small">{{ exportIds.length }} selected</span></div><div class="block-list"><PathTree :nodes="makeTree(visible, 'path', '/')" mode="parameters" :checked="exportIds" :active="selected?.id" @select="select" @check="(id, checked) => exportIds = checked ? [...exportIds, id] : exportIds.filter(value => value !== id)" /></div><p v-if="!blocks.length" class="empty">No authorized parameter blocks. Configure the OSE client identity and rights first.</p></aside>
    <section v-if="selected && schema" ref="editor" class="panel parameter-editor"><div class="section-title"><div><h2>{{ selected.path }}</h2><code>{{ schema.root }}</code></div><span class="badge" :data-state="dirty ? 'warning' : 'idle'">{{ dirty ? `${differences.length} local changes` : 'No local changes' }}</span></div><p class="muted small">Read at {{ latest?.observed_at }}. OSE remains the authority for effective access.</p><p v-if="stale" class="notice warning">The value is stale. Reload after OSE reconnects before applying.</p><div v-if="remoteChanged" class="notice warning"><strong>The remote value changed.</strong> Your draft has been preserved.<details><summary>Show latest remote value</summary><pre>{{ JSON.stringify(latest.value, null, 2) }}</pre></details></div><div class="editor-actions actions"><button class="quiet" :disabled="busy" @click="reload">Reload</button><button class="quiet" :disabled="busy || !dirty" @click="discard">Discard changes</button><button class="quiet" :disabled="busy" @click="showContract = !showContract">{{ showContract ? 'Hide contract' : 'Show contract' }}</button><button class="primary" :disabled="busy || stale || !connected || !selected.permissions.write" @click="apply">Apply</button></div><div v-if="error" ref="errorSummary" class="notice error validation-summary" role="alert" tabindex="-1"><strong>Parameter error</strong><p>{{ error }}</p><button v-if="validation" class="quiet" @click="revealError">Go to field</button></div><p class="notice subtle">Applying replaces the entire block. The last write processed by OSE wins; concurrent edits may be overwritten.</p><SchemaView v-if="showContract" :schema="schema" /><SchemaField :schema="schema" :type="schema.root" :model-value="draft" label="Value" :readonly="!selected.permissions.write || busy" :validation="validation" @update:model-value="draft = $event" /><details v-if="dirty" open><summary>Local changes</summary><div class="table-scroll"><table><thead><tr><th>Field</th><th>Base</th><th>Draft</th></tr></thead><tbody><tr v-for="change in differences" :key="change.pointer"><td class="mono">{{ change.pointer }}</td><td>{{ JSON.stringify(change.before) ?? 'Absent' }}</td><td>{{ JSON.stringify(change.after) ?? 'Absent' }}</td></tr></tbody></table></div></details><section v-if="preview" class="validation-preview"><h3>Validated normalized value</h3><p>{{ preview.encoded_bytes }} encoded bytes. {{ preview.normalized ? 'Wire encoding changes the input; the value below will be applied.' : 'The codec preserves the entered value.' }}</p><pre>{{ JSON.stringify(preview.value, null, 2) }}</pre></section></section>
    <section v-else class="panel empty large"><div class="empty-symbol">⌘</div><h2>Inspect a parameter block</h2><p>Select a readable block to open its typed SDL editor.</p><p class="muted">Apply validates your draft before writing it to OSE.</p></section>
  </div>
</template>
