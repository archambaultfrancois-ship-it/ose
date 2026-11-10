<script setup lang="ts">
import { computed, useId } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { statusLabel } from '../connections'
const props = defineProps<{ data: any; selected?: boolean }>()
const emit = defineEmits<{ 'port-hover': [message: string]; 'input-select': [component: string, port: string] }>()
const marker = useId()
const component = computed(() => props.data.kind === 'component')
const rows = computed(() => Math.max(props.data.inputs.length, props.data.outputs.length, 1))
const arrows = computed(() => (props.data.activations || []).flatMap((link: any, index: number) => {
  const from = props.data.inputs.findIndex((port: any) => port.port === link.input)
  const to = props.data.outputs.findIndex((port: any) => port.port === link.output)
  if (from < 0 || to < 0) return []
  const y1 = from * 64 + 32 + (link.event === 'timeout' ? 5 : -5), y2 = to * 64 + 32
  return [{ ...link, index, d: 'M 156 ' + y1 + ' C 195 ' + y1 + ', 205 ' + y2 + ', 240 ' + y2 }]
}))
function inspect(port: any) { emit('input-select', port.component, port.port) }
</script>
<template>
  <div class="component-node" :class="[data.kind, { selected, 'execution-component': component }]">
    <header><span class="node-icon">{{ data.kind === 'external' ? '↗' : data.kind === 'group' ? '▣' : '◇' }}</span><strong>{{ data.name }}</strong><small>{{ data.kind }}</small></header>
    <div class="node-trigger">{{ component ? 'Per-input callbacks · ' + data.activations.length + ' activation link(s)' : data.kind === 'group' ? 'Visual group · double-click to expand' : 'External publisher' }}</div>
    <div v-if="component" class="execution-ports" :style="{ minHeight: rows * 64 + 'px' }">
      <svg v-if="data.showActivations" class="activation-arrows" :viewBox="'0 0 400 ' + rows * 64" aria-label="Input event to output activation links">
        <defs><marker :id="marker" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto-start-reverse"><path d="M 0 0 L 6 3 L 0 6 z" fill="context-stroke" /></marker></defs>
        <path v-for="arrow in arrows" :key="arrow.index" :d="arrow.d" :class="arrow.event" :marker-end="'url(#' + marker + ')'"><title>{{ arrow.input }} · {{ arrow.event }} → {{ arrow.output }}</title></path>
      </svg>
      <div class="execution-inputs">
        <div v-for="port in data.inputs" :key="port.endpoint" class="node-port input execution-port" :data-mode="port.mode" :data-connection-status="port.assistance?.status" :title="port.assistance?.message" @pointerenter="port.assistance && emit('port-hover', port.endpoint + ' · ' + port.assistance.message)">
          <Handle v-if="port.connectable" :id="port.endpoint" type="target" :position="Position.Left" />
          <button class="nodrag nopan inspect-input" :aria-label="'Edit input ' + port.endpoint" @click.stop="inspect(port)">{{ port.name }}</button>
          <small class="mode-label">{{ port.label }}</small><code v-if="port.root">{{ port.root }}</code>
          <small v-if="port.assistance" class="connection-marker">{{ statusLabel[port.assistance.status as keyof typeof statusLabel] }}</small>
          <small v-else-if="port.reception?.subscriptions.length">{{ port.reception.subscriptions.length }} link(s) · {{ port.reception.policy.buffer }}</small>
        </div>
      </div>
      <div class="execution-outputs">
        <div v-for="port in data.outputs" :key="port.endpoint" class="node-port output execution-port" :data-connection-status="port.assistance?.status" :title="port.assistance?.message" @pointerenter="port.assistance && emit('port-hover', port.endpoint + ' · ' + port.assistance.message)">
          <span>{{ port.name }}</span><code>{{ port.root }}</code><small v-if="port.assistance">{{ statusLabel[port.assistance.status as keyof typeof statusLabel] }}</small>
          <Handle :id="port.endpoint" type="source" :position="Position.Right" />
        </div>
      </div>
    </div>
    <div v-else class="node-ports">
      <div v-for="port in data.inputs" :key="'i' + port.endpoint" class="node-port input" :data-connection-status="port.assistance?.status" :title="port.assistance?.message" @pointerenter="port.assistance && emit('port-hover', port.endpoint + ' · ' + port.assistance.message)">
        <Handle v-if="port.connectable !== false" :id="port.endpoint" type="target" :position="Position.Left" />
        <span>{{ port.name }}<small v-if="port.mode"> {{ port.mode === 'timer' ? port.label : port.mode === 'pull' ? 'Pull · passive' : 'Push' }}</small></span>
        <code>{{ port.root }}</code><small v-if="port.assistance" class="connection-marker">{{ statusLabel[port.assistance.status as keyof typeof statusLabel] }}</small><small v-else-if="port.reception?.subscriptions.length">{{ port.reception.subscriptions.length }} link(s) · {{ port.reception.conflict ? 'QoS conflict' : port.reception.policy.buffer }}</small>
      </div>
      <div v-for="port in data.outputs" :key="'o' + port.endpoint" class="node-port output" :data-connection-status="port.assistance?.status" :title="port.assistance?.message" @pointerenter="port.assistance && emit('port-hover', port.endpoint + ' · ' + port.assistance.message)"><span>{{ port.name }}</span><code>{{ port.root }}</code><small v-if="port.assistance" class="connection-marker">{{ statusLabel[port.assistance.status as keyof typeof statusLabel] }}</small><Handle :id="port.endpoint" type="source" :position="Position.Right" /></div>
    </div>
  </div>
</template>
<style scoped>
.node-port[data-connection-status=compatible] { background: #eaf7f0; outline: 1px solid #3a926a; }
.node-port[data-connection-status=incompatible] { background: #fff0ef; color: #9b3535; }
.node-port[data-connection-status=unknown] { background: #fff8e6; outline: 1px dashed #b68a2e; }
.connection-marker { font-weight: 600; }
.execution-component { width: 400px; }
.execution-ports { position: relative; display: grid; grid-template-columns: 156px 156px; justify-content: space-between; }
.execution-port { height: 64px; display: flex; flex-direction: column; justify-content: center; align-items: stretch; gap: 2px; overflow: visible; }
.execution-port > span, .inspect-input { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.execution-port code { max-width: 100%; }
.inspect-input { background: none; border: 0; text-align: left; padding: 0; color: #3159a4; font-size: 11px; }
.mode-label { color: #536d91; }
.execution-port[data-mode=pull] .mode-label { color: #6d4ea0; }
.execution-port[data-mode=timer] .mode-label { color: #966514; }
.activation-arrows { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.activation-arrows > path { fill: none; stroke-width: 1.5; pointer-events: stroke; }
.activation-arrows .message { stroke: #4770bd; }
.activation-arrows .timeout { stroke: #b48222; stroke-dasharray: 4 2; }
</style>
