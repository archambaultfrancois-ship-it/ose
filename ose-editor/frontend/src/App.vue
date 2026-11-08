<script setup lang="ts">
import { computed, ref, onBeforeUnmount, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Dialog from 'primevue/dialog'
import { useProject } from './store'
import { atPath, nodeLabel } from './hints'
import GraphView from './components/GraphView.vue'
import JsonTree from './components/JsonTree.vue'
import SdlTree from './components/SdlTree.vue'
import TablesView from './components/TablesView.vue'
import SourceEditor from './components/SourceEditor.vue'
import ContractView from './components/ContractView.vue'
const store = useProject(), view = ref('Graph'), filter = ref(''), deployment = ref(''), bottom = ref('Diagnostics'), changes = ref<any[]>([]), usages = ref<any[]>([])
const selected = computed(() => store.selection && store.project?.documents[store.selection.file])
const node = computed(() => { let result = selected.value?.tree; for (const key of store.selection?.path || []) result = result?.children?.find((child: any) => child.name === key); return result })
const documents = computed(() => Object.values(store.project?.documents || {}).filter((doc: any) => doc.id.toLowerCase().includes(filter.value.toLowerCase())) as any[])
const errors = computed(() => [...(store.project?.diagnostics || []), ...((store.job?.stale || store.job?.revision !== store.project?.revision) ? [] : store.job?.diagnostics || store.project?.validation?.diagnostics || [])])
const dirtySource = computed(() => Object.keys(store.sourceDrafts).length > 0)
const validationState = computed(() => store.job ? store.job.revision !== store.project?.revision ? 'Validation out of date' : store.job.status : store.project?.validation?.revision === store.project?.revision ? store.project.validation.status : 'Not validated')
watch(() => store.project?.selected_deployment, value => { if (!deployment.value) deployment.value = value || '' })
watch(deployment, value => { if (value && store.project && store.project.selected_deployment !== value) store.command({ action: 'select_deployment', file: value }, false) })
function addComponent() { const name = window.prompt('New component name'); if (name) store.command({ action: 'add_component', name }) }
function duplicate() {
  const selection = store.selection, path = selection?.path
  if (selection?.file !== store.project.model || path?.length !== 2) return
  if (path[0] === 'components') {
    const name = window.prompt('New component name (handler and parameter references are shared)')
    if (name) store.command({ action: 'add_component', name, copy: path[1] })
  } else if (path[0] === 'subscriptions') {
    const subscription = atPath(selected.value.value, path)
    const name = window.prompt('New subscription ID')?.trim()
    if (!name) return
    const to = window.prompt('Destination input (component.port; must be unused)', subscription.to)?.trim()
    if (to) store.command({ action: 'duplicate_subscription', copy: subscription.id, name, to })
  }
}
function rename() {
  const selection = store.selection; if (!selection) return
  const name = window.prompt('New name; indexed references will be included in the preview'); if (!name) return
  const path = selection.path || []
  if (selection.sdl) store.command({ action: 'rename', kind: 'sdl', file: selection.file, path: selection.sdl, name })
  else if (selection.file === store.project.model && path[0] === 'components' && path.length === 2) store.command({ action: 'rename', kind: 'component', old: path[1], name })
  else if (selection.file === store.project.model && path[0] === 'components' && path.length === 4 && ['inputs', 'outputs'].includes(path[2])) store.command({ action: 'rename', kind: 'port', old: path[1] + '.' + path[3], direction: path[2], name })
  else if (selection.file === store.project.model && path[0] === 'subscriptions' && path.length === 2) store.command({ action: 'rename', kind: 'subscription', old: atPath(selected.value.value, path).id, name })
  else if (selection.file === store.project.model && path[0] === 'shared_parameters' && path.length === 2) store.command({ action: 'rename', kind: 'parameter', old: path[1], name })
  else if (path.length) store.command({ action: 'rename_key', file: selection.file, path, name })
}
function remove() { const path = store.selection?.path || []; if (store.selection?.file === store.project.model && path[0] === 'components' && path.length === 2) store.command({ action: 'remove_component', name: path[1] }); else if (path.length) store.command({ action: 'delete', file: store.selection?.file, path }) }
function addDeployment() { const name = window.prompt('New deployment filename'); if (name) store.command({ action: 'deployment', name, copy: deployment.value }) }
function addSchema() { const name = window.prompt('New schema filename, relative to the application root'); if (name) store.command({ action: 'create_document', name, text: name.endsWith('.sdl') ? 'message NewMessage {\n}\n' : '{"format":"sdl-schema","messages":{}}\n' }) }
function exportSchema() { if (!store.selection) return; const format = window.prompt('Export format: sdl, json, yaml or xsd', 'json'); if (!format) return; const name = window.prompt('New filename outside schema inputs (conversion may normalize expressions, aliases and comments)', 'exports/schema.sdl.' + format); if (name) store.command({ action: 'export_schema', file: store.selection.file, format, name }) }
async function showChanges() { bottom.value = 'Changes'; const result = await store.api('changes'); changes.value = result.changes }
async function showUsages() { bottom.value = 'Usages'; const name = store.selection?.path?.at(-1) || ''; const result = await store.api('usages?name=' + encodeURIComponent(name)); usages.value = result.items }
async function apply() { const result = await store.apply(); if (result) { for (const [file, text] of Object.entries(store.sourceDrafts)) { const doc = await store.api('document?file=' + encodeURIComponent(file)); if (doc.text === text) delete store.sourceDrafts[file] } } }
function unload(event: BeforeUnloadEvent) { if (store.project?.dirty || dirtySource.value) event.preventDefault() }
window.addEventListener('beforeunload', unload)
onBeforeUnmount(() => window.removeEventListener('beforeunload', unload))
</script>
<template>
  <main v-if="!store.project" class="login-shell">
    <form class="login-card" @submit.prevent="store.load"><div class="brand-mark">◇</div><p class="eyebrow">OSE / AUTHORING</p><h1>Model Editor</h1><p>Compose your application.<br>Keep its sources in your hands.</p><label>Access token<InputText v-model="store.token" type="password" autofocus autocomplete="off" /></label><small>Read the token from the file printed by <code>ose-editor</code>.</small><Button type="submit" label="Open workspace" :loading="store.busy" /><p v-if="store.error" role="alert" class="error">{{ store.error }}</p></form>
  </main>
  <div v-else class="app-shell">
    <header class="app-header"><div class="brand"><span class="brand-mark">◇</span><div><strong>OSE <span>Model Editor</span></strong><small>{{ store.project.name }} <span v-if="store.project.dirty || dirtySource">● Unsaved changes</span></small></div></div>
      <div class="header-actions"><Select v-model="deployment" :options="store.project.deployments" placeholder="Select deployment" aria-label="Deployment" /><span class="validation-badge" :data-state="validationState">{{ validationState }}</span><Button label="Validate" severity="secondary" :disabled="!!store.pending || !!store.job && ['queued','running'].includes(store.job.status)" @click="store.validate(deployment)" /><Button label="Save" :disabled="!!store.pending || dirtySource" @click="store.save()" /><Button label="Save draft" severity="secondary" :disabled="!!store.pending || dirtySource" @click="store.save(true)" /></div></header>
    <div v-if="store.error" class="error-banner" role="alert">{{ store.error }}<Button label="Refresh project" variant="text" @click="store.load" /></div>
    <div v-if="store.project.recovery" class="error-banner">An interrupted save needs recovery.<Button label="Restore previous files" @click="store.guard(async () => { store.project = await store.api('recovery', { choice: 'restore' }) })" /><Button label="Complete save" @click="store.guard(async () => { store.project = await store.api('recovery', { choice: 'complete' }) })" /></div>
    <div v-if="store.project.draft_recovery" class="error-banner">An unsaved authoring draft was recovered.<Button label="Restore draft" @click="store.guard(async () => { store.project = await store.api('draft-recovery', { revision: store.project.revision, restore: true }) })" /><Button label="Discard recovered draft" severity="secondary" @click="store.guard(async () => { store.project = await store.api('draft-recovery', { revision: store.project.revision, restore: false }) })" /></div>
    <div class="workspace-grid">
      <aside class="explorer"><div class="panel-heading"><h2>PROJECT</h2><span>{{ documents.length }} files</span></div><InputText v-model="filter" placeholder="Filter files…" aria-label="Filter files" /><div class="explorer-actions"><Button label="+ Component" size="small" severity="secondary" @click="addComponent" /><Button label="+ Schema" size="small" severity="secondary" @click="addSchema" /></div>
        <div class="file-list"><details v-for="doc in documents" :key="doc.id" open><summary @click.stop="store.select(doc.id, [])"><span>{{ doc.format === 'sdl' ? '◈' : '▤' }}</span><button @click.stop="store.select(doc.id, [])">{{ doc.id }}</button><span v-if="doc.dirty" class="dirty-dot">●</span></summary>
          <template v-if="doc.tree"><div v-for="child in doc.tree.children" :key="child.name" class="explorer-section"><button @click="store.select(doc.id, child.path)">{{ child.name }} <small>{{ child.children.length || '' }}</small></button><button v-for="item in child.children.slice(0, 80)" :key="item.name" class="explorer-object" @click="store.select(doc.id, item.path)">{{ nodeLabel(item) }}<small>{{ item.kind }}</small></button></div></template>
          <button v-for="item in doc.nodes || []" :key="item.path" class="explorer-object" @click="store.select(doc.id, undefined, item.path)">{{ item.name }} <small>{{ item.kind }}</small></button>
          <small v-if="doc.stale" class="error">Source has syntax errors</small>
        </details></div>
        <Button label="Duplicate deployment" severity="secondary" size="small" @click="addDeployment" />
      </aside>
      <section class="main-workspace"><nav class="workspace-tabs"><button v-for="tab in ['Graph', 'Tables', 'Sources']" :key="tab" :class="{ active: view === tab }" @click="view = tab">{{ tab }}</button><div class="history-actions"><Button label="Undo" size="small" severity="secondary" :disabled="!store.project.can_undo" @click="store.history()" /><Button label="Redo" size="small" severity="secondary" :disabled="!store.project.can_redo" @click="store.history(true)" /></div></nav>
        <div class="workspace-content"><GraphView v-if="view === 'Graph'" /><TablesView v-else-if="view === 'Tables'" /><SourceEditor v-else-if="store.selection" :file="store.selection.file" :format="selected?.format || 'json'" /></div>
        <section class="diagnostics-panel"><nav><button v-for="tab in ['Diagnostics', 'Changes', 'Usages']" :key="tab" :class="{ active: bottom === tab }" @click="tab === 'Changes' ? showChanges() : tab === 'Usages' ? showUsages() : bottom = tab">{{ tab }}<span v-if="tab === 'Diagnostics'">{{ errors.length }}</span></button><div v-if="store.job && ['queued', 'running'].includes(store.job.status)" class="job-progress">{{ store.job.progress || 'Validating…' }}<Button label="Cancel" variant="text" size="small" @click="store.cancel" /></div></nav>
          <div class="diagnostics-content"><template v-if="bottom === 'Diagnostics'"><p v-if="!errors.length" class="empty-state">{{ validationState === 'passed' ? 'Validation passed for this revision.' : 'No source syntax errors. Validate to check OSE and SDL semantics.' }}</p><button v-for="(diagnostic, index) in errors" :key="index" class="diagnostic-row" @click="diagnostic.file && store.select(diagnostic.file, [])"><strong>{{ diagnostic.origin }}</strong><span>{{ diagnostic.message }}</span><code>{{ diagnostic.file }}</code></button></template><template v-else-if="bottom === 'Changes'"><article v-for="change in changes" :key="change.file"><strong>{{ change.file }}</strong><pre>{{ change.diff }}</pre></article><p v-if="!changes.length" class="empty-state">No changes to maintained source files.</p></template><template v-else><button v-for="(usage, index) in usages" :key="index" class="diagnostic-row" @click="store.select(usage.file, []); view = 'Sources'"><code>{{ usage.file }}:{{ usage.line }}</code><span>{{ usage.text }}</span></button><small>Source occurrences; external consumers are not assessed.</small></template></div>
        </section>
      </section>
      <aside class="inspector"><div class="panel-heading"><h2>INSPECTOR</h2><button @click="view = 'Sources'" title="Show source">↗ Source</button></div><template v-if="selected"><p class="inspector-location">{{ store.selection?.file }}</p><h3>{{ nodeLabel(node) || store.selection?.path?.at(-1) || (store.selection?.sdl ? 'SDL declaration' : 'Document properties') }}</h3><div class="inspector-actions"><Button label="Rename references" size="small" severity="secondary" @click="rename" /><Button label="Duplicate" size="small" severity="secondary" @click="duplicate" /><Button label="Remove" size="small" severity="danger" @click="remove" /></div><p v-if="selected.stale" class="error">This projection is stale. Repair the source to resume structured edits.</p><JsonTree v-if="node" :key="store.selection?.file + JSON.stringify(store.selection?.path) + store.project.revision" :node="node" :file="selected.id" :role="selected.role" :readonly="selected.stale" /><SdlTree v-else-if="selected.nodes" :nodes="selected.nodes" :file="selected.id" :selected="store.selection?.sdl" :readonly="selected.stale" /><p v-else class="empty-state">Select an object to inspect.</p><Button v-if="selected.role === 'schema'" label="Export schema format" size="small" severity="secondary" @click="exportSchema" /><ContractView /><details v-if="selected.comments?.length" class="comments-panel"><summary>Source comments</summary><label v-for="(comment, index) in selected.comments" :key="index">Comment {{ Number(index) + 1 }}<textarea :value="comment.value" @change="event => store.command({ action: 'comment', file: selected.id, index, value: (event.target as HTMLTextAreaElement).value })" /></label></details></template></aside>
    </div>
    <footer class="status-bar"><span>● Local authoring · OSE model v1 / SDL</span><span>{{ store.project.documents[store.project.model]?.stale ? 'Stale model projection' : 'Sources synchronized' }}</span><code>{{ store.project.revision.slice(0, 8) }}</code></footer>
  </div>
  <Dialog :visible="!!store.pending" modal header="Review changes" :style="{ width: 'min(900px, 94vw)' }" @update:visible="visible => { if (!visible) store.pending = null }"><template v-if="store.pending"><p>Apply this operation as one reversible change.</p><article v-for="change in store.pending.changes" :key="change.file" class="diff-file"><h3>{{ change.file }}</h3><pre>{{ change.diff }}</pre><p v-if="change.identity_changes.length" class="error">Field identity changes: {{ change.identity_changes }}</p></article><p v-if="!store.pending.changes.length">Editor layout or workspace metadata changes.</p><details v-if="store.pending.handlers_to_review.length"><summary>Handlers to review</summary><code v-for="handler in store.pending.handlers_to_review" :key="handler" class="handler-path">{{ handler }}</code></details><small>{{ store.pending.external_consumers }}</small></template><template #footer><Button label="Cancel" severity="secondary" @click="store.pending = null" /><Button label="Apply changes" :loading="store.busy" @click="apply" /></template></Dialog>
</template>
