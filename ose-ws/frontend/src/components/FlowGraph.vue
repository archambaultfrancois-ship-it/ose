<script setup lang="ts">
import { computed, nextTick, ref, useId } from 'vue'
import { layout, nodeHeight, nodeWidth, type FlowNode, type FlowEdge, type Topology } from '../flow-graph'
import LinkContract from './LinkContract.vue'
const props = defineProps<{ topology?: Topology | null; components: any[]; epoch?: string | null; error?: string | null; matching: string[]; selected?: string | null }>()
const emit = defineEmits<{ select: [name: string] }>()
const graph = computed(() => layout(props.topology))
const reports = computed(() => new Map(props.components.map(row => [row.name, row])))
const matches = computed(() => new Set(props.matching))
const zoom = ref(1), labels = ref(true), viewport = ref<HTMLElement>()
const selectedLinkId = ref<string | null>(null), linkDetails = ref<HTMLElement>()
const selectedLink = computed(() => graph.value.edges.find(edge => edge.id === selectedLinkId.value))
const policyFields = [
  ['buffer', 'Buffer'], ['capacity', 'Capacity'], ['overflow', 'Overflow'],
  ['ack', 'Acknowledgement'], ['max_retries', 'Max retries'],
  ['ack_timeout_ms', 'ACK timeout (ms)'], ['backoff_multiplier', 'Backoff multiplier'],
  ['wait_timeout_ms', 'Wait timeout (ms)'],
]
async function selectLink(id: string) {
  selectedLinkId.value = id
  await nextTick()
  linkDetails.value?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
}
const marker = 'flow-arrow-' + useId().replace(/[^a-zA-Z0-9_-]/g, '')
function fit() { zoom.value = Math.min(1, (viewport.value?.clientWidth || graph.value.width) / graph.value.width) }
function rate(value: any) { return value == null ? '—' : Number(value).toLocaleString('en', { maximumFractionDigits: 1 }) }
function state(node: FlowNode) { return node.kind === 'external' ? 'EXTERNAL TOPIC' : reports.value.get(node.name)?.state || 'UNAVAILABLE' }
function schedule(node: FlowNode) {
  return node.kind === 'external' ? node.root || 'External publisher' : node.trigger?.mode === 'periodic' ? `Periodic · ${node.trigger.period_ms} ms` : node.trigger?.mode === 'on_change' ? 'On change' : 'Trigger unavailable'
}
function short(value: string, limit = 28) { return value.length > limit ? value.slice(0, limit - 1) + '…' : value }
function edgeTitle(edge: FlowEdge) {
  const policy = edge.policy || {}
  return `${edge.id}\n${edge.from} → ${edge.to}${edge.root ? '\nSDL type: ' + edge.root : ''}\n${edge.routing || 'broker'} · ${policy.transport || 'tcp'}${policy.buffer ? '\nBuffer: ' + policy.buffer : ''}${policy.capacity ? ' · capacity ' + policy.capacity : ''}${policy.overflow ? ' · ' + policy.overflow : ''}${policy.ack ? '\nACK: ' + policy.ack : ''}`
}
</script>
<template>
  <section class="panel flow-panel" aria-label="Application flow graph">
    <div class="section-title"><div><h2>Application flow</h2><p class="muted small">{{ graph.nodes.filter(n => n.kind === 'component').length }} components · {{ graph.edges.length }} subscriptions · source → consumer</p></div><div class="actions" v-if="graph.nodes.length"><label class="inline"><input type="checkbox" v-model="labels" /> Link labels</label><button class="quiet" aria-label="Zoom out" :disabled="zoom <= 0.1" @click="zoom = Math.max(0.1, zoom - 0.1)">−</button><span class="small">{{ Math.round(zoom * 100) }}%</span><button class="quiet" aria-label="Zoom in" :disabled="zoom >= 2" @click="zoom = Math.min(2, zoom + 0.1)">+</button><button class="quiet" @click="fit">Fit</button></div></div>
    <p v-if="!topology" class="muted">Flow topology is unavailable. Connect to an OSE broker or coordinator with observation metadata.</p>
    <template v-else>
      <p v-if="error" class="notice warning" role="status">Last known topology. Live component states are unavailable.</p>
      <div v-if="graph.nodes.length" class="flow-viewport" ref="viewport" tabindex="0" aria-label="Flow graph; scroll to explore and use zoom controls to resize">
        <svg :width="graph.width * zoom" :height="graph.height * zoom" :viewBox="`0 0 ${graph.width} ${graph.height}`" role="group" aria-label="Components and directed subscriptions">
          <defs><marker :id="marker" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8babb5" /></marker></defs>
          <g v-for="edge in graph.edges" :key="edge.id" class="flow-edge" :class="{ direct: edge.routing === 'direct', selected: selectedLinkId === edge.id }" role="button" tabindex="0" :aria-label="`Subscription ${edge.id}: ${edge.from} → ${edge.to}`" :aria-expanded="selectedLinkId === edge.id" @click="selectLink(edge.id)" @keydown.enter.prevent="selectLink(edge.id)" @keydown.space.prevent="selectLink(edge.id)">
            <title>{{ edgeTitle(edge) }}</title><path class="flow-hit" :d="edge.path" vector-effect="non-scaling-stroke" /><path class="flow-line" :d="edge.path" :marker-end="`url(#${marker})`" />
            <text v-if="labels" :x="edge.x" :y="edge.y - 7" text-anchor="middle">{{ short(edge.id, 22) }}</text>
          </g>
          <g v-for="node in graph.nodes" :key="node.id" class="flow-node" :class="{ external: node.kind === 'external', selected: selected === node.name && node.kind === 'component', dimmed: node.kind === 'component' && !matches.has(node.name) }" :data-state="node.kind === 'external' ? 'external' : reports.get(node.name)?.severity || 'error'" :transform="`translate(${node.x}, ${node.y})`" :role="node.kind === 'component' ? 'button' : 'group'" :tabindex="node.kind === 'component' ? 0 : undefined" :aria-label="`${node.name}: ${state(node)}`" @click="node.kind === 'component' && emit('select', node.name)" @keydown.enter.prevent="node.kind === 'component' && emit('select', node.name)" @keydown.space.prevent="node.kind === 'component' && emit('select', node.name)">
            <title>{{ node.name }}
{{ state(node) }} · {{ schedule(node) }}
{{ node.kind === 'component' ? 'Select to inspect component diagnostics.' : 'Declared external topic.' }}</title>
            <rect :width="nodeWidth" :height="nodeHeight" rx="9" /><circle cx="16" cy="21" r="4" />
            <text x="28" y="25" class="flow-name">{{ short(node.name, 25) }}</text><text x="16" y="45" class="flow-state">{{ state(node) }}</text>
            <text x="16" y="64">{{ short(schedule(node), 32) }}</text>
            <text v-if="node.kind === 'component'" x="16" y="84">In {{ rate(reports.get(node.name)?.rates?.in) }}/s · Out {{ rate(reports.get(node.name)?.rates?.out) }}/s</text>
          </g>
        </svg>
      </div>
      <p v-else class="muted">No components in this topology.</p>
      <section v-if="selectedLink" ref="linkDetails" class="flow-link-details" aria-label="Subscription details" @keydown.esc="selectedLinkId = null">
        <div class="section-title"><div><h3>Subscription details</h3><code>{{ selectedLink.id }}</code></div><button class="quiet" @click="selectedLinkId = null">Close link details</button></div>
        <dl>
          <div><dt>From</dt><dd><code>{{ selectedLink.from }}</code></dd></div>
          <div><dt>To</dt><dd><code>{{ selectedLink.to }}</code></dd></div>
          <div><dt>SDL type</dt><dd>{{ selectedLink.root || 'Unavailable' }}</dd></div>
          <div><dt>Routing</dt><dd>{{ selectedLink.routing || 'broker' }}</dd></div>
          <div><dt>Transport</dt><dd>{{ selectedLink.policy?.transport || 'tcp' }}</dd></div>
          <template v-for="[key, label] in policyFields" :key="key"><div v-if="selectedLink.policy?.[key] != null"><dt>{{ label }}</dt><dd>{{ selectedLink.policy[key] }}</dd></div></template>
        </dl>
        <LinkContract :subscription="selectedLink.id" :epoch="epoch" />
      </section>
      <p class="muted small flow-note">Select a component for diagnostics. Tap or click a link or its label for ports, SDL type and delivery policy. Dashed links use direct routing. Filters highlight matching components.</p>
    </template>
  </section>
</template>
<style scoped>
.flow-panel{padding:18px;margin-bottom:18px;min-width:0}.flow-panel .section-title{gap:12px;flex-wrap:wrap}.flow-panel h2{margin-bottom:4px}.flow-panel p{margin-bottom:0}.flow-panel .actions{gap:8px;flex-wrap:wrap}.flow-viewport{overflow:auto;max-height:560px;border:1px solid #e3ebf0;border-radius:7px;background:radial-gradient(#dce8eb 1px,transparent 1px);background-size:18px 18px;margin-top:14px}.flow-viewport svg{display:block}.flow-note{margin-top:12px}.flow-edge path{fill:none;stroke:#8babb5;stroke-width:1.7}.flow-edge.direct path{stroke-dasharray:6 4}.flow-edge:hover path{stroke:#247a81;stroke-width:3}.flow-edge text{fill:#577786;font-size:11px;paint-order:stroke;stroke:#fff;stroke-width:5px;stroke-linejoin:round}.flow-node{cursor:pointer;--tone:#7b909f}.flow-node rect{fill:#fff;stroke:var(--tone);stroke-width:1.5}.flow-node circle{fill:var(--tone)}.flow-node text{font-size:11px;fill:#708693}.flow-node .flow-name{font-size:13px;font-weight:650;fill:#27495a}.flow-node .flow-state{font-size:10px;font-weight:650;fill:var(--tone)}.flow-node[data-state=ok]{--tone:#238d7d}.flow-node[data-state=warning]{--tone:#a57622}.flow-node[data-state=error]{--tone:#ba415a}.flow-node[data-state=waiting]{--tone:#527eaf}.flow-node.external{cursor:default;--tone:#7c6bad}.flow-node.external rect{fill:#f8f6fc;stroke-dasharray:5 3}.flow-node.dimmed{opacity:.3}.flow-node.selected rect,.flow-node:focus rect,.flow-node:not(.external):hover rect{stroke:#176d78;stroke-width:3;fill:#f1faf9}.flow-node:focus{outline:none}
.flow-edge{cursor:pointer}.flow-edge .flow-hit{stroke:transparent;stroke-width:24;stroke-dasharray:none;pointer-events:stroke}.flow-edge .flow-line{pointer-events:none}.flow-edge.selected .flow-line,.flow-edge:focus .flow-line{stroke:#176d78;stroke-width:3}.flow-edge.selected text,.flow-edge:focus text{fill:#176d78;font-weight:650}.flow-edge:focus{outline:none}
.flow-link-details{margin-top:14px;padding:14px;background:#f1faf9;border:1px solid #b9d9d6;border-radius:7px;overflow-wrap:anywhere;scroll-margin:16px}.flow-link-details h3{margin:0 0 5px}.flow-link-details button{min-height:44px}.flow-link-details dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:12px;margin:0}.flow-link-details dt{font-size:11px;color:#708693;margin-bottom:3px}.flow-link-details dd{margin:0;font-size:13px;color:#27495a}
</style>
