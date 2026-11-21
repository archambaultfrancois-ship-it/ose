<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { VueFlow, useVueFlow, useNodesInitialized } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import ComponentNode from './ComponentNode.vue'
import SubsystemFrame from './SubsystemFrame.vue'
import ComponentEditor from './ComponentEditor.vue'
import SubsystemTools from './SubsystemTools.vue'
import ToolbarMenu from './ToolbarMenu.vue'
import AutomaticEdge from './AutomaticEdge.vue'
import { graphModel, subsystemGraph, definitionScopes } from '../subsystems'
import { useProject } from '../store'
import { topology, downstream, nodeSize, type LinkStyle } from '../graph'
import { layoutGraph, layoutPositions, type FrameLayout } from '../layout'
import { assessConnection, inputs, publishers, statusLabel, type Publisher } from '../connections'
defineEmits<{ 'add-component': [] }>()
const props = defineProps<{ navigationRequest?: number }>()
const store = useProject(), nodes = ref<any[]>([]), edges = ref<any[]>([]), search = ref(''), labels = ref(true), scope = ref('all'), layoutBusy = ref(false)
const linkStyle = ref<LinkStyle>('default')
const showActivations = ref(false)
const linkStyles = [{ label: 'Curved', value: 'default' }, { label: 'Straight', value: 'straight' }, { label: 'Right-angle', value: 'smoothstep' }]
const model = computed(() => graphModel(store.project))
const expandedSubsystems = computed<string[]>(() => store.project?.workspace.views?.expanded_subsystems || [])
const displayModel = computed(() => subsystemGraph(store.project, expandedSubsystems.value))
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
const { fitView, getViewport, setViewport, getNodes, getSelectedNodes, selectionKeyCode } = useVueFlow()
const routingNodes = computed(() => getNodes.value.map(node => {
  const size = nodeSize(node), frame = node.data.kind === 'definition'
  return { id: node.id, name: node.data.name, frame, x: node.position.x, y: node.position.y,
    width: frame ? size.width : node.dimensions.width || size.width, height: frame ? size.height : node.dimensions.height || size.height }
}))
selectionKeyCode.value = ['Shift', 'Control', 'Meta']
let overviewViewport: ReturnType<typeof getViewport> | null = null
watch(() => store.editingComponent, async (name, previous) => {
  if (name && !previous) overviewViewport = getViewport()
  else if (!name && previous && overviewViewport) {
    await nextTick()
    await setViewport(overviewViewport)
  }
})
const selectedGroups = computed(() => getSelectedNodes.value.filter(node => node.data.kind === 'group' && !node.data.subsystem).map(node => node.data.name))
const hasGroups = computed(() => Object.keys(store.project?.workspace.groups || {}).length > 0 || Object.keys(model.value?.subsystems || {}).length > 0)
const hasExpandedGroups = computed(() => expandedSubsystems.value.length > 0 || Object.values(store.project?.workspace.groups || {}).some((group: any) => !group.collapsed))
const selectedMembers = computed(() => getSelectedNodes.value.filter(node => (node.data.kind === 'component' || node.data.subsystem) && !node.data.scope?.length).map(node => node.data.name))
const selectedComponents = computed(() => getSelectedNodes.value.filter(node => node.data.kind === 'component' && !node.data.scope?.length).map(node => node.data.name))
const extraction = computed(() => {
  const members = new Set<string>(selectedComponents.value), seen = new Set<string>()
  function collect(name: string) {
    if (seen.has(name)) return
    seen.add(name)
    const group = store.project.workspace.groups[name]
    for (const member of group?.members || []) members.add(member)
    for (const child of group?.groups || []) collect(child)
  }
  selectedGroups.value.forEach(collect)
  return [...members].every(name => store.project.documents[store.project.model]?.value?.components?.[name]) ? [...members] : []
})
// Inspector focus is a single object; Vue Flow owns the graph's multiple selection.
let graphInspection: typeof store.selection = null
let graphRevision = 0
const worker = new Worker(new URL('../layout.worker.ts', import.meta.url), { type: 'module' })
watch(() => store.project?.revision, () => { connectionFeedback.value = '' })
watch(() => [store.project?.model, store.project?.revision, store.project?.validation, labels.value, search.value, scope.value, linkStyle.value, showActivations.value, store.selection, drag.value], async () => {
  const revision = ++graphRevision
  const graph = topology(displayModel.value, store.project?.workspace, labels.value, linkStyle.value, showActivations.value)
  const inspectionChanged = store.selection !== graphInspection
  const selectedIds = new Set(getSelectedNodes.value.map(node => node.id))
  const chosen = ['components', 'subsystems'].includes(store.selection?.path?.[0] || '') ? store.selection?.path?.[1] || '' : ''
  const inspectedId = chosen ? (model.value?.components[chosen]?._subsystem ? 'subsystem:' : 'component:') + chosen : store.selection?.path?.[0] === 'external_topics' ? 'external:' + store.selection.path[1] : ''
  const selectedName = graph.nodes.find(node => node.data.model === store.selection?.file && node.data.localName === chosen)?.data.name || chosen
  const highlighted = chosen && scope.value !== 'all' ? downstream(displayModel.value, selectedName, scope.value) : null
  graph.nodes.forEach(node => { node.class = search.value && !node.data.name.toLowerCase().includes(search.value.toLowerCase()) || highlighted && !highlighted.has(node.data.name) ? 'dimmed' : ''; node.selected = inspectionChanged ? node.id === inspectedId || !!chosen && (node.data.model === store.selection?.file && node.data.localName === chosen || !!node.data.members?.includes(chosen)) : selectedIds.has(node.id) })
  if (drag.value) for (const node of graph.nodes) {
    for (const port of node.data.inputs) if (drag.value.type === 'source') {
      port.assistance = assessConnection(store.project, drag.value, port.endpoint)
    }
    for (const port of node.data.outputs) if (drag.value.type === 'target') {
      port.assistance = assessConnection(store.project, { endpoint: port.endpoint, external: node.data.kind === 'external' }, drag.value.endpoint)
    }
  }
  graph.edges.forEach((edge: any) => { edge.selected = store.selection?.file === store.project?.model && store.selection?.path?.[0] === 'subscriptions' && edge.data.index === Number(store.selection.path[1]) })
  nodes.value = graph.nodes
  graphInspection = store.selection
  // Register the replacement nodes before Vue Flow validates their edges.
  await nextTick()
  if (revision === graphRevision) edges.value = graph.edges
}, { immediate: true, deep: false })
const nodesInitialized = useNodesInitialized()
let pendingNavigation = false, pendingDefinitionFit = false
async function revealNavigation() {
  if (pendingDefinitionFit && nodesInitialized.value && !store.editingComponent) {
    pendingDefinitionFit = false
    await nextTick()
    await fitView({ padding: 0.15, maxZoom: 1 })
  }
  if (!pendingNavigation || !nodesInitialized.value || store.editingComponent) return
  await nextTick()
  const path = store.selection?.path || []
  let ids: string[] = []
  if (['components', 'subsystems'].includes(path[0])) {
    const node = nodes.value.find(node => node.id === 'component:' + path[1] || node.id === 'subsystem:' + path[1] || node.data.members?.includes(path[1]))
    if (node) ids = [node.id]
  } else if (path[0] === 'subscriptions') {
    const edge = edges.value.find(edge => edge.data.index === Number(path[1]))
    if (edge) ids = [edge.source, edge.target]
  } else if (path[0] === 'external_topics') ids = ['external:' + path[1]]
  pendingNavigation = false
  if (ids.length) { search.value = ''; await nextTick(); fitView({ nodes: ids, padding: 0.35, maxZoom: 1 }) }
}
watch(() => props.navigationRequest, request => { pendingNavigation = !!request; revealNavigation() }, { immediate: true })
async function layout() {
  layoutBusy.value = true
  const graph = layoutGraph(nodes.value, edges.value)
  worker.onmessage = async event => {
    layoutBusy.value = false
    if (event.data.error) { store.error = event.data.error; return }
    const positions = { ...store.project.workspace.positions }
    for (const [name, position] of Object.entries(layoutPositions(event.data.graph))) {
      if (!store.project.workspace.pinned.includes(name)) positions[name] = position
    }
    const frameBounds = { ...store.project.workspace.views?.frame_bounds }
    for (const node of nodes.value) if (node.data.kind === 'definition') delete frameBounds[node.data.name]
    await store.command({ action: 'workspace', positions, ...(store.project.workspace.views?.frame_bounds ? { views: { ...store.project.workspace.views, frame_bounds: frameBounds } } : {}) }, false)
    setTimeout(() => fitView({ padding: 0.15 }), 100)
  }
  worker.postMessage({ id: Date.now(), graph })
}
function validConnection(connection: any) {
  // Vue Flow also checks existing edges when the visible topology changes.
  // Projected subscriptions already resolve to real ports, including private
  // ports revealed by expansion. Authoring checks apply to new connections.
  if (connection.data?.sub) return true
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
  if (data.kind === 'definition' || data.kind === 'group' && !data.subsystem) return
  const path = data.kind === 'external' ? ['external_topics', data.name] : data.subsystem ? ['subsystems', data.localName] : ['components', data.localName]
  if (data.scope?.length) store.selection = { file: data.model, path }
  else store.select(data.model || store.project.model, path)
  graphInspection = store.selection
}
function inspectInput(component: string, port: string) {
  const data = nodes.value.find(node => node.data.name === component)?.data
  if (data?.scope?.length) { openScope(data.scope); component = data.localName }
  store.select(store.project.model, ['components', component, 'inputs', port])
  graphInspection = store.selection
}
function openScope(scope: string[]) { scope.forEach(name => store.openSubsystem(name)) }
function editComponent(name: string) {
  const data = nodes.value.find(node => node.data.name === name)?.data
  if (!data) return
  openScope(data.scope || [])
  store.editComponent(data.localName)
}
function openDefinition(name: string) {
  const data = nodes.value.find(node => node.data.name === name)?.data
  if (!data) { openScope(name.split('.')); return }
  openScope(data.scope || [])
  store.openSubsystem(data.localName)
}
async function setExpanded(names: string[]) {
  pendingDefinitionFit = true
  const result = await store.command({ action: 'workspace', views: { ...store.project.workspace.views, expanded_subsystems: [...new Set(names)] } }, false)
  if (result) { await nextTick(); await revealNavigation() }
  else pendingDefinitionFit = false
}
function collapseSubsystem(name: string) { setExpanded(expandedSubsystems.value.filter(path => path !== name && !path.startsWith(name + '.'))) }
function expand(event: any) {
  if (event.node.data.kind === 'definition') { collapseSubsystem(event.node.data.name); return }
  if (event.node.data.subsystem) { setExpanded([...expandedSubsystems.value, ...definitionScopes(store.project, event.node.data.name)]); return }
  if (event.node.data.kind === 'component') { editComponent(event.node.data.name); return }
  if (event.node.data.kind !== 'group') return
  const groups = JSON.parse(JSON.stringify(store.project.workspace.groups))
  groups[event.node.data.name].collapsed = false
  store.command({ action: 'workspace', groups })
}
function toggleGroups() {
  const groups = JSON.parse(JSON.stringify(store.project.workspace.groups))
  const collapsed = hasExpandedGroups.value
  Object.values(groups).forEach((group: any) => { group.collapsed = collapsed })
  store.command({ action: 'workspace', groups, ...(Object.keys(model.value.subsystems || {}).length || expandedSubsystems.value.length ? { views: { ...store.project.workspace.views, expanded_subsystems: collapsed ? [] : Object.keys(model.value.subsystems || {}).flatMap(name => definitionScopes(store.project, name)) } } : {}) })
}
function resizeFrame(layout: FrameLayout) {
  store.command({ action: 'workspace', positions: { ...store.project.workspace.positions, ...layout.positions },
    views: { ...store.project.workspace.views, frame_bounds: { ...store.project.workspace.views?.frame_bounds, ...layout.frameBounds } } }, false)
}
type GroupDrag = { id: string; model: string; revision: string; start: { x: number; y: number }; members: Map<string, { x: number; y: number }> }
let groupDrag: GroupDrag | null = null
function startPosition(event: any) {
  groupDrag = null
  if (event.node.data.kind !== 'definition') return
  const members = new Set(event.node.data.members)
  groupDrag = { id: event.node.id, model: store.project.model, revision: store.project.revision,
    start: { ...event.node.position }, members: new Map(nodes.value.filter(node => members.has(node.data.name))
      .map(node => [node.id, { ...node.position }])) }
}
function movePosition(event: any) {
  if (!groupDrag || groupDrag.id !== event.node.id) return
  if (groupDrag.model !== store.project.model || groupDrag.revision !== store.project.revision) { groupDrag = null; return }
  const dx = event.node.position.x - groupDrag.start.x, dy = event.node.position.y - groupDrag.start.y
  for (const node of nodes.value) {
    const original = groupDrag.members.get(node.id)
    if (original) node.position = { x: original.x + dx, y: original.y + dy }
  }
}
function position(event: any) {
  const positions = { ...store.project.workspace.positions }, node = event.node
  const moved = [node]
  const frameBounds = { ...store.project.workspace.views?.frame_bounds }
  if (node.data.kind === 'definition') {
    if (!groupDrag || groupDrag.id !== node.id || groupDrag.model !== store.project.model || groupDrag.revision !== store.project.revision) {
      groupDrag = null; return
    }
    movePosition(event)
    moved.push(...nodes.value.filter(member => groupDrag?.members.has(member.id)))
  }
  for (const member of moved) {
    const frame = member.data.kind === 'definition'
    const name = frame || member.data.kind === 'component' || member.data.subsystem ? member.data.name : member.id
    positions[name] = { x: member.position.x + (frame ? 24 : 0), y: member.position.y + (frame ? 100 : 0) }
    if (frame && frameBounds[name]) frameBounds[name] = { ...frameBounds[name], x: member.position.x, y: member.position.y }
  }
  groupDrag = null
  store.command({ action: 'workspace', positions, ...(Object.keys(frameBounds).length ? { views: { ...store.project.workspace.views, frame_bounds: frameBounds } } : {}) }, false)
}

function group() {
  const members = selectedMembers.value, children = selectedGroups.value
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
    <nav v-if="store.modelTrail.length" class="subsystem-breadcrumbs" aria-label="Subsystem hierarchy"><button v-for="(item, index) in store.modelTrail" :key="index" @click="store.parentModel(index)">{{ index ? ' / ' : '' }}{{ item.name }}</button></nav>
    <p v-if="store.project.model !== store.project.root_model" class="definition-notice">Editing shared definition: {{ store.project.model }}. Changes affect every instance of this definition.</p>
    <SubsystemTools :selected="extraction" :group="selectedGroups.length === 1 ? selectedGroups[0] : undefined" />
    <div class="graph-toolbar">
      <div class="toolbar-actions">
      <Button label="Add component" :disabled="!!store.pending || store.busy" @click="$emit('add-component')" />
      <InputText v-model="search" placeholder="Find a component…" aria-label="Find a component" />
      <Button ref="connectButton" label="Connect ports" severity="secondary" :disabled="!!store.pending" @click="openAssistant()" />
      </div>
      <Button class="auto-layout" label="Auto layout" severity="secondary" :loading="layoutBusy" @click="layout" />
      <div class="toolbar-menus">
      <ToolbarMenu label="Groups">
        <Button label="Group selection" severity="secondary" :disabled="!selectedMembers.length && !selectedGroups.length" @click="group" />
        <Button :label="hasExpandedGroups ? 'Collapse all groups' : 'Expand all groups'" severity="secondary" :disabled="!hasGroups" @click="toggleGroups" />
      </ToolbarMenu>
      <ToolbarMenu label="Display">
        <label>Dependency highlighting<Select v-model="scope" :options="['all', 'upstream', 'downstream', 'both']" append-to="self" aria-label="Dependency highlighting" /></label>
        <label class="inline"><input type="checkbox" v-model="labels"> Link labels</label>
        <label v-if="model?.version === 2" class="inline"><input type="checkbox" v-model="showActivations" aria-label="Show activation links"> Activation links</label>
        <label>Link style<Select v-model="linkStyle" :options="linkStyles" option-label="label" option-value="value" append-to="self" aria-label="Link style" /></label>
      </ToolbarMenu>
      </div>
    </div>
    <form v-if="assistant" class="connection-assistant" aria-label="Connect ports" @submit.prevent="previewConnection" @keydown.esc.stop="closeAssistant">
      <label>Publisher<select ref="sourceSelect" v-model="sourceKey" aria-label="Connection publisher"><option value="" disabled>Choose an output</option><option v-for="source in sourceOptions" :key="source.key" :value="source.key">{{ source.endpoint }}{{ source.external ? ' (external topic)' : '' }}</option></select></label>
      <label>Destination input<select v-model="target" aria-label="Connection destination" aria-describedby="connection-assessment"><option value="" disabled>Choose an input</option><option v-for="option in targetOptions" :key="option.endpoint" :value="option.endpoint">{{ option.endpoint }}{{ option.assessment ? ' — ' + statusLabel[option.assessment.status] : '' }}</option></select></label>
      <label>Subscription ID<input ref="idInput" v-model="subscriptionId" aria-label="Connection subscription ID" required /></label>
      <div class="actions"><Button type="submit" label="Preview connection" :disabled="!assessment || assessment.status === 'incompatible' || !subscriptionId.trim() || !!store.pending || store.busy" /><Button type="button" label="Cancel connection" severity="secondary" @click="closeAssistant" /></div>
      <p id="connection-assessment" role="status" :data-status="assessment?.status">{{ assessment ? statusLabel[assessment.status] + ' · ' + assessment.message : 'Choose a publisher and destination. Inputs with existing producers can receive another connection.' }}</p>
    </form>
    <p v-if="connectionFeedback" class="connection-feedback" role="status">{{ connectionFeedback }}</p>
    <VueFlow v-model:nodes="nodes" v-model:edges="edges" fit-view-on-init :is-valid-connection="validConnection" :multi-selection-key-code="['Shift', 'Control', 'Meta']" :delete-key-code="null" :min-zoom="0.15" :max-zoom="2" @nodes-initialized="revealNavigation" @node-click="selected" @node-double-click="expand" @edge-click="event => store.select(event.edge.data.model || store.project.model, ['subscriptions', String(event.edge.data.index)])" @connect-start="startConnection" @connect-end="drag = null" @connect="connect" @node-drag-start="startPosition" @node-drag="movePosition" @node-drag-stop="position">
      <template #edge-default="edgeProps"><AutomaticEdge v-bind="edgeProps" :nodes="routingNodes" /></template>
      <template #edge-straight="edgeProps"><AutomaticEdge v-bind="edgeProps" :nodes="routingNodes" /></template>
      <template #edge-smoothstep="edgeProps"><AutomaticEdge v-bind="edgeProps" :nodes="routingNodes" /></template>
      <template #node-subsystem-frame="nodeProps"><SubsystemFrame v-bind="nodeProps" @resize="resizeFrame" /></template>
      <template #node-component="nodeProps"><ComponentNode v-bind="nodeProps" @port-hover="message => connectionFeedback = message" @input-select="inspectInput" @edit-component="editComponent" @open-subsystem="openDefinition" /></template>
      <Background :gap="24" pattern-color="#c8d5e5" /><MiniMap pannable zoomable /><Controls />
    </VueFlow>
    <p class="graph-caption">{{ Object.keys(store.project.documents[store.project.model]?.value?.components || {}).length }} components · {{ Object.keys(model?.subsystems || {}).length }} subsystems · {{ model?.subscriptions?.length || 0 }} subscriptions · {{ selectedComponents.length }} components selected · {{ selectedGroups.length }} groups selected · Shift/Ctrl/Cmd + click to select multiple · Shift + drag to select an area · drag between ports to connect</p>
    </div>
  </div>
</template>

<style scoped>
.graph-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); gap: 6px; padding: 6px 12px; }
.toolbar-actions, .toolbar-menus { display: flex; align-items: center; gap: 6px; min-width: 0; }
.toolbar-actions :deep(.p-button), .graph-toolbar > :deep(.p-button), .toolbar-actions :deep(.p-inputtext) { min-height: 28px; padding: 5px 8px; font-size: 11px !important; line-height: 16px; }
.toolbar-actions :deep(.p-button) { flex-shrink: 0; white-space: nowrap; }
.toolbar-actions :deep(.p-inputtext) { flex: 1; min-width: 60px; max-width: 160px; width: 100%; }
.auto-layout { grid-column: 2; justify-self: center; white-space: nowrap; }
.toolbar-menus { grid-column: 3; justify-self: end; }
@container (max-width: 680px) { .toolbar-actions { grid-column: 1 / -1; flex-wrap: wrap; } }
@container (max-width: 420px) { .toolbar-menus { grid-column: 1 / -1; } }
.subsystem-breadcrumbs, .definition-notice { padding: 8px 16px; margin: 0; font-size: 12px; background: #edf3fc; }
.subsystem-breadcrumbs button { border: none; background: none; color: #3159a4; cursor: pointer; }
.graph-overview { display: flex; flex-direction: column; flex: 1; min-height: 0; container-type: inline-size; }
.graph-overview :deep(.vue-flow) { flex: 1; min-height: 0; }
.connection-assistant { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; padding: 14px 16px; background: #f7f9fd; border-bottom: 1px solid #dce4ef; }
.connection-assistant label { min-width: 150px; flex: 1; font-size: 11px; }
.connection-assistant select, .connection-assistant input { width: 100%; min-height: 34px; padding: 6px; border: 1px solid #bcc9dd; border-radius: 5px; background: white; color: #334c6e; }
.connection-assistant p { width: 100%; font-size: 12px; line-height: 1.5; }
.connection-assistant [data-status=incompatible] { color: #a63838; }
.connection-assistant [data-status=compatible] { color: #187452; }
.connection-feedback { margin: 0; padding: 10px 16px; font-size: 12px; background: #f3f6fc; border-bottom: 1px solid #dce4ef; }
</style>
