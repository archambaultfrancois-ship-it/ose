<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import ComponentNode from './ComponentNode.vue'
import ComponentEditor from './ComponentEditor.vue'
import { useProject } from '../store'
import { topology, downstream, type LinkStyle } from '../graph'
import { layoutGraph } from '../layout'
import { assessConnection, inputs, publishers, statusLabel, type Publisher } from '../connections'
const store = useProject(), nodes = ref<any[]>([]), edges = ref<any[]>([]), search = ref(''), labels = ref(true), scope = ref('all'), layoutBusy = ref(false)
const linkStyle = ref<LinkStyle>('default')
const showActivations = ref(false)
const linkStyles = [{ label: 'Curved', value: 'default' }, { label: 'Straight', value: 'straight' }, { label: 'Right-angle', value: 'smoothstep' }]
const model = computed(() => store.project?.documents[store.project.model]?.value)
const drag = ref<{ endpoint: string; type: string; external: boolean } | null>(null)
const connectionFeedback = ref(''), assistant = ref(false), sourceKey = ref(''), target = ref(''), subscriptionId = ref('')
const sourceSelect = ref<HTMLSelectElement>(), idInput = ref<HTMLInputElement>()
const connectButton = ref<{ $el: HTMLButtonElement }>()
const sourceOptions = computed(() => publishers(model.value).map(source => ({ ...source, key: JSON.stringify([source.external, source.endpoint]) })))
const chosenSource = computed(() => sourceOptions.value.find(source => source.key === sourceKey.value))
const targetOptions = computed(() => inputs(model.value).map(endpoint => ({ endpoint,
  assessment: chosenSource.value ? assessConnection(store.project, chosenSource.value, endpoint) : null })))
const assessment = computed(() => chosenSource.value && target.value ? assessConnection(store.project, chosenSource.value, target.value) : null)
// Configure key filters through the store: Vue Flow's prop validator only
// accepts booleans even though its selection engine supports key arrays.
const { fitView, getViewport, setViewport, getSelectedNodes, selectionKeyCode } = useVueFlow()
selectionKeyCode.value = ['Shift', 'Control', 'Meta']
let overviewViewport: ReturnType<typeof getViewport> | null = null
watch(() => store.editingComponent, async (name, previous) => {
  if (name && !previous) overviewViewport = getViewport()
  else if (!name && previous && overviewViewport) {
    await nextTick()
    await setViewport(overviewViewport)
  }
})
const selectedGroups = computed(() => getSelectedNodes.value.filter(node => node.data.kind === 'group').map(node => node.data.name))
const hasGroups = computed(() => Object.keys(store.project?.workspace.groups || {}).length > 0)
const hasExpandedGroups = computed(() => Object.values(store.project?.workspace.groups || {}).some((group: any) => !group.collapsed))
const selectedComponents = computed(() => getSelectedNodes.value.filter(node => node.data.kind === 'component').map(node => node.data.name))
// Inspector focus is a single object; Vue Flow owns the graph's multiple selection.
let graphInspection: typeof store.selection = null
const worker = new Worker(new URL('../layout.worker.ts', import.meta.url), { type: 'module' })
watch(() => store.project?.revision, () => { connectionFeedback.value = '' })
watch(() => [store.project?.revision, store.project?.validation, labels.value, search.value, scope.value, linkStyle.value, showActivations.value, store.selection, drag.value], () => {
  const graph = topology(model.value, store.project?.workspace, labels.value, linkStyle.value, showActivations.value)
  const inspectionChanged = store.selection !== graphInspection
  const selectedIds = new Set(getSelectedNodes.value.map(node => node.id))
  const chosen = store.selection?.path?.[0] === 'components' ? store.selection.path[1] : ''
  const inspectedId = chosen ? 'component:' + chosen : store.selection?.path?.[0] === 'external_topics' ? 'external:' + store.selection.path[1] : ''
  const highlighted = chosen && scope.value !== 'all' ? downstream(model.value, chosen, scope.value) : null
  graph.nodes.forEach(node => { node.class = search.value && !node.data.name.toLowerCase().includes(search.value.toLowerCase()) || highlighted && !highlighted.has(node.data.name) ? 'dimmed' : ''; node.selected = inspectionChanged ? node.id === inspectedId : selectedIds.has(node.id) })
  if (drag.value) for (const node of graph.nodes) {
    for (const port of node.data.inputs) if (drag.value.type === 'source') {
      port.assistance = assessConnection(store.project, drag.value, port.endpoint)
    }
    for (const port of node.data.outputs) if (drag.value.type === 'target') {
      port.assistance = assessConnection(store.project, { endpoint: port.endpoint, external: node.data.kind === 'external' }, drag.value.endpoint)
    }
  }
  nodes.value = graph.nodes; edges.value = graph.edges
  graphInspection = store.selection
}, { immediate: true, deep: false })
async function layout() {
  layoutBusy.value = true
  const graph = layoutGraph(nodes.value, edges.value)
  worker.onmessage = async event => {
    layoutBusy.value = false
    if (event.data.error) { store.error = event.data.error; return }
    const positions = { ...store.project.workspace.positions }
    event.data.graph.children.forEach((node: any) => { const name = node.id.startsWith('component:') ? node.id.slice(10) : node.id; if (!store.project.workspace.pinned.includes(name)) positions[name] = { x: node.x, y: node.y } })
    await store.command({ action: 'workspace', positions }, false)
    setTimeout(() => fitView({ padding: 0.15 }), 100)
  }
  worker.postMessage({ id: Date.now(), graph })
}
function validConnection(connection: any) {
  return !!connection.sourceHandle && !!connection.targetHandle && assessConnection(store.project,
    { endpoint: connection.sourceHandle, external: connection.source.startsWith('external:') }, connection.targetHandle).status !== 'incompatible'
}
function startConnection(event: any) {
  if (!event.handleId || !event.nodeId || !event.handleType) return
  drag.value = { endpoint: event.handleId, type: event.handleType, external: event.nodeId.startsWith('external:') }
  connectionFeedback.value = 'Choose a port: ✓ compatible · ? contract unchecked · × unavailable. Hover a port for details.'
}
async function closeAssistant() {
  assistant.value = false
  await nextTick()
  connectButton.value?.$el.focus()
}
async function openAssistant(source?: Publisher, destination = '') {
  assistant.value = true
  connectionFeedback.value = ''
  sourceKey.value = source ? JSON.stringify([source.external, source.endpoint]) : sourceOptions.value[0]?.key || ''
  target.value = destination
  subscriptionId.value = ''
  await nextTick()
  if (destination) idInput.value?.focus()
  else sourceSelect.value?.focus()
}
function connect(connection: any) {
  const source = { endpoint: connection.sourceHandle, external: connection.source.startsWith('external:') }
  const result = assessConnection(store.project, source, connection.targetHandle)
  if (result.status === 'incompatible') { connectionFeedback.value = result.message; return }
  openAssistant(source, connection.targetHandle)
}
async function previewConnection() {
  if (store.pending || store.busy || !chosenSource.value || !target.value || !subscriptionId.value.trim() || assessment.value?.status === 'incompatible') return
  const prepared = await store.command({ action: 'connect', name: subscriptionId.value.trim(),
    from: chosenSource.value.endpoint, to: target.value, external: chosenSource.value.external })
  if (prepared) assistant.value = false
}
function selected(event: any) {
  const data = event.node.data
  if (data.kind === 'group') return
  store.select(store.project.model, data.kind === 'external' ? ['external_topics', data.name] : ['components', data.name])
  graphInspection = store.selection
}
function inspectInput(component: string, port: string) {
  store.select(store.project.model, ['components', component, 'inputs', port])
  graphInspection = store.selection
}
function expand(event: any) {
  if (event.node.data.kind === 'component') { store.editComponent(event.node.data.name); return }
  if (event.node.data.kind !== 'group') return
  const groups = JSON.parse(JSON.stringify(store.project.workspace.groups))
  groups[event.node.data.name].collapsed = false
  store.command({ action: 'workspace', groups })
}
function toggleGroups() {
  const groups = JSON.parse(JSON.stringify(store.project.workspace.groups))
  const collapsed = hasExpandedGroups.value
  Object.values(groups).forEach((group: any) => { group.collapsed = collapsed })
  store.command({ action: 'workspace', groups })
}
function position(event: any) { const positions = { ...store.project.workspace.positions }; const node = event.node; positions[node.data.kind === 'component' ? node.data.name : node.id] = node.position; store.command({ action: 'workspace', positions }, false) }
function group() {
  const members = selectedComponents.value, children = selectedGroups.value
  if (!members.length && !children.length) { store.error = 'Select components or groups before creating a visual group.'; return }
  const name = window.prompt('Visual group name')?.trim()
  if (!name) return
  const groups = JSON.parse(JSON.stringify(store.project.workspace.groups))
  if (Object.hasOwn(groups, name)) { store.error = 'A visual group with this name already exists.'; return }
  // Move the selected items into their new parent instead of leaving overlaps.
  Object.values(groups).forEach((group: any) => {
    group.members = (group.members || []).filter((member: string) => !members.includes(member))
    if (group.groups) group.groups = group.groups.filter((child: string) => !children.includes(child))
  })
  store.command({ action: 'workspace', groups: {
    ...groups, [name]: { members, ...(children.length ? { groups: children } : {}), collapsed: true },
  } })
}
onBeforeUnmount(() => worker.terminate())
</script>
<template>
  <div class="graph-workspace">
    <ComponentEditor v-if="store.editingComponent" />
    <div v-show="!store.editingComponent" class="graph-overview">
    <div class="graph-toolbar"><InputText v-model="search" placeholder="Find a component…" aria-label="Find a component" /><Select v-model="scope" :options="['all', 'upstream', 'downstream', 'both']" aria-label="Dependency highlighting" /><Button ref="connectButton" label="Connect ports" severity="secondary" :disabled="!!store.pending" @click="openAssistant()" /><Button label="Auto layout" severity="secondary" :loading="layoutBusy" @click="layout" /><Button label="Group selection" severity="secondary" :disabled="!selectedComponents.length && !selectedGroups.length" @click="group" /><Button :label="hasExpandedGroups ? 'Collapse all groups' : 'Expand all groups'" severity="secondary" :disabled="!hasGroups" @click="toggleGroups" /><div class="link-display-controls"><label class="inline"><input type="checkbox" v-model="labels"> Link labels</label><label v-if="model?.version === 2" class="inline"><input type="checkbox" v-model="showActivations" aria-label="Show activation links"> Activation links</label><Select v-model="linkStyle" :options="linkStyles" option-label="label" option-value="value" size="small" class="link-style-select" aria-label="Link style" /></div></div>
    <form v-if="assistant" class="connection-assistant" aria-label="Connect ports" @submit.prevent="previewConnection" @keydown.esc.stop="closeAssistant">
      <label>Publisher<select ref="sourceSelect" v-model="sourceKey" aria-label="Connection publisher"><option value="" disabled>Choose an output</option><option v-for="source in sourceOptions" :key="source.key" :value="source.key">{{ source.endpoint }}{{ source.external ? ' (external topic)' : '' }}</option></select></label>
      <label>Destination input<select v-model="target" aria-label="Connection destination" aria-describedby="connection-assessment"><option value="" disabled>Choose an input</option><option v-for="option in targetOptions" :key="option.endpoint" :value="option.endpoint">{{ option.endpoint }}{{ option.assessment ? ' — ' + statusLabel[option.assessment.status] : '' }}</option></select></label>
      <label>Subscription ID<input ref="idInput" v-model="subscriptionId" aria-label="Connection subscription ID" required /></label>
      <div class="actions"><Button type="submit" label="Preview connection" :disabled="!assessment || assessment.status === 'incompatible' || !subscriptionId.trim() || !!store.pending || store.busy" /><Button type="button" label="Cancel connection" severity="secondary" @click="closeAssistant" /></div>
      <p id="connection-assessment" role="status" :data-status="assessment?.status">{{ assessment ? statusLabel[assessment.status] + ' · ' + assessment.message : 'Choose a publisher and destination. Inputs with existing producers can receive another connection.' }}</p>
    </form>
    <p v-if="connectionFeedback" class="connection-feedback" role="status">{{ connectionFeedback }}</p>
    <VueFlow v-model:nodes="nodes" v-model:edges="edges" fit-view-on-init :is-valid-connection="validConnection" :multi-selection-key-code="['Shift', 'Control', 'Meta']" :delete-key-code="null" :min-zoom="0.15" :max-zoom="2" @node-click="selected" @node-double-click="expand" @edge-click="event => store.select(store.project.model, ['subscriptions', String(event.edge.data.index)])" @connect-start="startConnection" @connect-end="drag = null" @connect="connect" @node-drag-stop="position">
      <template #node-component="nodeProps"><ComponentNode v-bind="nodeProps" @port-hover="message => connectionFeedback = message" @input-select="inspectInput" @edit-component="store.editComponent" /></template>
      <Background :gap="24" pattern-color="#c8d5e5" /><MiniMap pannable zoomable /><Controls />
    </VueFlow>
    <p class="graph-caption">{{ Object.keys(model?.components || {}).length }} components · {{ model?.subscriptions?.length || 0 }} subscriptions · {{ selectedComponents.length }} components selected · {{ selectedGroups.length }} groups selected · Shift/Ctrl/Cmd + click to select multiple · Shift + drag to select an area · drag between ports to connect</p>
    </div>
  </div>
</template>

<style scoped>
.graph-overview { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.graph-overview :deep(.vue-flow) { flex: 1; min-height: 0; }
.connection-assistant { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; padding: 14px 16px; background: #f7f9fd; border-bottom: 1px solid #dce4ef; }
.connection-assistant label { min-width: 150px; flex: 1; font-size: 11px; }
.connection-assistant select, .connection-assistant input { width: 100%; min-height: 34px; padding: 6px; border: 1px solid #bcc9dd; border-radius: 5px; background: white; color: #334c6e; }
.connection-assistant p { width: 100%; font-size: 12px; line-height: 1.5; }
.connection-assistant [data-status=incompatible] { color: #a63838; }
.connection-assistant [data-status=compatible] { color: #187452; }
.connection-feedback { margin: 0; padding: 10px 16px; font-size: 12px; background: #f3f6fc; border-bottom: 1px solid #dce4ef; }
.link-display-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; max-width: 100%; }
.link-display-controls .inline { height: 20px; line-height: 14px; }
.link-display-controls input { margin: 0; }
.link-style-select { width: 94px; height: 20px; align-items: center; font-size: 10px !important; }
.link-style-select :deep(.p-select-label) { padding: 2px 6px; font-size: 10px; line-height: 14px; }
.link-style-select :deep(.p-select-dropdown) { width: 18px; }
.link-style-select :deep(.p-select-dropdown-icon) { width: 10px; height: 10px; }
</style>
