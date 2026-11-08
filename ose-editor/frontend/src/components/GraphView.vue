<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import ComponentNode from './ComponentNode.vue'
import { useProject } from '../store'
import { topology, downstream, type LinkStyle } from '../graph'
import { layoutGraph } from '../layout'
const store = useProject(), nodes = ref<any[]>([]), edges = ref<any[]>([]), search = ref(''), labels = ref(true), scope = ref('all'), layoutBusy = ref(false)
const linkStyle = ref<LinkStyle>('default')
const linkStyles = [{ label: 'Curved', value: 'default' }, { label: 'Straight', value: 'straight' }, { label: 'Right-angle', value: 'smoothstep' }]
const model = computed(() => store.project?.documents[store.project.model]?.value)
// Configure key filters through the store: Vue Flow's prop validator only
// accepts booleans even though its selection engine supports key arrays.
const { fitView, getSelectedNodes, selectionKeyCode } = useVueFlow()
selectionKeyCode.value = ['Shift', 'Control', 'Meta']
const selectedGroups = computed(() => getSelectedNodes.value.filter(node => node.data.kind === 'group').map(node => node.data.name))
const hasExpandedGroups = computed(() => Object.values(store.project?.workspace.groups || {}).some((group: any) => !group.collapsed))
const selectedComponents = computed(() => getSelectedNodes.value.filter(node => node.data.kind === 'component').map(node => node.data.name))
// Inspector focus is a single object; Vue Flow owns the graph's multiple selection.
let graphInspection: typeof store.selection = null
const worker = new Worker(new URL('../layout.worker.ts', import.meta.url), { type: 'module' })
watch(() => [store.project?.revision, labels.value, search.value, scope.value, linkStyle.value, store.selection], () => {
  const graph = topology(model.value, store.project?.workspace, labels.value, linkStyle.value)
  const inspectionChanged = store.selection !== graphInspection
  const selectedIds = new Set(getSelectedNodes.value.map(node => node.id))
  const chosen = store.selection?.path?.[0] === 'components' ? store.selection.path[1] : ''
  const inspectedId = chosen ? 'component:' + chosen : store.selection?.path?.[0] === 'external_topics' ? 'external:' + store.selection.path[1] : ''
  const highlighted = chosen && scope.value !== 'all' ? downstream(model.value, chosen, scope.value) : null
  graph.nodes.forEach(node => { node.class = search.value && !node.data.name.toLowerCase().includes(search.value.toLowerCase()) || highlighted && !highlighted.has(node.data.name) ? 'dimmed' : ''; node.selected = inspectionChanged ? node.id === inspectedId : selectedIds.has(node.id) })
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
function connect(connection: any) { const name = window.prompt('Subscription ID'); if (name) store.command({ action: 'connect', name, from: connection.sourceHandle, to: connection.targetHandle, external: connection.source.startsWith('external:') }) }
function selected(event: any) {
  const data = event.node.data
  if (data.kind === 'group') return
  store.select(store.project.model, data.kind === 'external' ? ['external_topics', data.name] : ['components', data.name])
  graphInspection = store.selection
}
function expand(event: any) {
  if (event.node.data.kind !== 'group') return
  const groups = JSON.parse(JSON.stringify(store.project.workspace.groups))
  groups[event.node.data.name].collapsed = false
  store.command({ action: 'workspace', groups })
}
function collapseAll() {
  const groups = JSON.parse(JSON.stringify(store.project.workspace.groups))
  Object.values(groups).forEach((group: any) => { group.collapsed = true })
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
    <div class="graph-toolbar"><InputText v-model="search" placeholder="Find a component…" aria-label="Find a component" /><Select v-model="scope" :options="['all', 'upstream', 'downstream', 'both']" aria-label="Dependency highlighting" /><Button label="Auto layout" severity="secondary" :loading="layoutBusy" @click="layout" /><Button label="Group selection" severity="secondary" :disabled="!selectedComponents.length && !selectedGroups.length" @click="group" /><Button label="Collapse all groups" severity="secondary" :disabled="!hasExpandedGroups" @click="collapseAll" /><div class="link-display-controls"><label class="inline"><input type="checkbox" v-model="labels"> Link labels</label><Select v-model="linkStyle" :options="linkStyles" option-label="label" option-value="value" size="small" class="link-style-select" aria-label="Link style" /></div></div>
    <VueFlow v-model:nodes="nodes" v-model:edges="edges" fit-view-on-init :multi-selection-key-code="['Shift', 'Control', 'Meta']" :delete-key-code="null" :min-zoom="0.15" :max-zoom="2" @node-click="selected" @node-double-click="expand" @edge-click="event => store.select(store.project.model, ['subscriptions', String(event.edge.data.index)])" @connect="connect" @node-drag-stop="position">
      <template #node-component="nodeProps"><ComponentNode v-bind="nodeProps" /></template>
      <Background :gap="24" pattern-color="#c8d5e5" /><MiniMap pannable zoomable /><Controls />
    </VueFlow>
    <p class="graph-caption">{{ Object.keys(model?.components || {}).length }} components · {{ model?.subscriptions?.length || 0 }} subscriptions · {{ selectedComponents.length }} components selected · {{ selectedGroups.length }} groups selected · Shift/Ctrl/Cmd + click to select multiple · Shift + drag to select an area · drag between ports to connect</p>
  </div>
</template>

<style scoped>
.link-display-controls { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.link-display-controls .inline { height: 20px; line-height: 14px; }
.link-display-controls input { margin: 0; }
.link-style-select { width: 94px; height: 20px; align-items: center; font-size: 10px !important; }
.link-style-select :deep(.p-select-label) { padding: 2px 6px; font-size: 10px; line-height: 14px; }
.link-style-select :deep(.p-select-dropdown) { width: 18px; }
.link-style-select :deep(.p-select-dropdown-icon) { width: 10px; height: 10px; }
</style>
