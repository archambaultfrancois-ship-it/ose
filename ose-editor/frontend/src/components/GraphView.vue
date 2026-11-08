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
import { topology, downstream } from '../graph'
const store = useProject(), nodes = ref<any[]>([]), edges = ref<any[]>([]), search = ref(''), labels = ref(true), scope = ref('all'), layoutBusy = ref(false)
const model = computed(() => store.project?.documents[store.project.model]?.value)
const { fitView, getSelectedNodes } = useVueFlow()
const worker = new Worker(new URL('../layout.worker.ts', import.meta.url), { type: 'module' })
watch(() => [store.project?.revision, labels.value, search.value, scope.value, store.selection], () => {
  const graph = topology(model.value, store.project?.workspace, labels.value)
  const chosen = store.selection?.path?.[0] === 'components' ? store.selection.path[1] : ''
  const highlighted = chosen && scope.value !== 'all' ? downstream(model.value, chosen, scope.value) : null
  graph.nodes.forEach(node => { node.class = search.value && !node.data.name.toLowerCase().includes(search.value.toLowerCase()) || highlighted && !highlighted.has(node.data.name) ? 'dimmed' : ''; node.selected = node.data.name === chosen })
  nodes.value = graph.nodes; edges.value = graph.edges
}, { immediate: true, deep: false })
async function layout() {
  layoutBusy.value = true
  const graph = { id: 'root', layoutOptions: { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.spacing.nodeNode': '70', 'elk.layered.spacing.nodeNodeBetweenLayers': '100' },
    children: nodes.value.map(node => ({ id: node.id, width: 270, height: 80 + (node.data.inputs.length + node.data.outputs.length) * 34,
      layoutOptions: { 'elk.portConstraints': 'FIXED_ORDER' }, ports: [...node.data.inputs.map((p: any) => ({ id: node.id + ':' + p.endpoint, width: 8, height: 8, properties: { 'port.side': 'WEST' } })), ...node.data.outputs.map((p: any) => ({ id: node.id + ':' + p.endpoint, width: 8, height: 8, properties: { 'port.side': 'EAST' } }))] })),
    edges: edges.value.map(edge => ({ id: edge.id, sources: [edge.source + ':' + edge.sourceHandle], targets: [edge.target + ':' + edge.targetHandle] })) }
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
function selected(event: any) { const data = event.node.data; if (data.kind === 'group') { const groups = JSON.parse(JSON.stringify(store.project.workspace.groups)); groups[data.name].collapsed = false; store.command({ action: 'workspace', groups }, false) } else store.select(store.project.model, data.kind === 'external' ? ['external_topics', data.name] : ['components', data.name]) }
function position(event: any) { const positions = { ...store.project.workspace.positions }; const node = event.node; positions[node.data.kind === 'component' ? node.data.name : node.id] = node.position; store.command({ action: 'workspace', positions }, false) }
function group() { const members = getSelectedNodes.value.filter(node => node.data.kind === 'component').map(node => node.data.name); if (!members.length) { store.error = 'Select components with Shift before creating a visual group.'; return }; const name = window.prompt('Visual group name'); if (name) store.command({ action: 'workspace', groups: { ...store.project.workspace.groups, [name]: { members, collapsed: true } } }) }
onBeforeUnmount(() => worker.terminate())
</script>
<template>
  <div class="graph-workspace">
    <div class="graph-toolbar"><InputText v-model="search" placeholder="Find a component…" aria-label="Find a component" /><Select v-model="scope" :options="['all', 'upstream', 'downstream', 'both']" aria-label="Dependency highlighting" /><Button label="Auto layout" severity="secondary" :loading="layoutBusy" @click="layout" /><Button label="Group selection" severity="secondary" @click="group" /><label class="inline"><input type="checkbox" v-model="labels"> Link labels</label></div>
    <VueFlow v-model:nodes="nodes" v-model:edges="edges" fit-view-on-init :delete-key-code="null" :min-zoom="0.15" :max-zoom="2" @node-click="selected" @edge-click="event => store.select(store.project.model, ['subscriptions', String(event.edge.data.index)])" @connect="connect" @node-drag-stop="position">
      <template #node-component="nodeProps"><ComponentNode v-bind="nodeProps" /></template>
      <Background :gap="24" pattern-color="#c8d5e5" /><MiniMap pannable zoomable /><Controls />
    </VueFlow>
    <p class="graph-caption">{{ Object.keys(model?.components || {}).length }} components · {{ model?.subscriptions?.length || 0 }} subscriptions · select a component or link to inspect · drag between ports to connect</p>
  </div>
</template>
