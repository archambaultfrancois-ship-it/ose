<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { statusLabel } from '../connections'
defineProps<{ data: any; selected?: boolean }>()
const emit = defineEmits<{ 'port-hover': [message: string] }>()
</script>
<template>
  <div class="component-node" :class="[data.kind, { selected }]">
    <header><span class="node-icon">{{ data.kind === 'external' ? '↗' : data.kind === 'group' ? '▣' : '◇' }}</span><strong>{{ data.name }}</strong><small>{{ data.kind }}</small></header>
    <div class="node-trigger">{{ data.trigger?.mode === 'periodic' ? '◷ ' + (data.trigger.period_ms?.$number || data.trigger.period_ms) + ' ms' : data.kind === 'group' ? 'Visual group · double-click to expand' : data.kind === 'external' ? 'External publisher' : '↯ on change' }}</div>
    <div class="node-ports">
      <div v-for="port in data.inputs" :key="'i' + port.endpoint" class="node-port input" :data-connection-status="port.assistance?.status" :title="port.assistance?.message" @pointerenter="port.assistance && emit('port-hover', port.endpoint + ' · ' + port.assistance.message)"><Handle :id="port.endpoint" type="target" :position="Position.Left" /><span>{{ port.name }}<small v-if="!port.required"> optional</small></span><code>{{ port.root }}</code><small v-if="port.assistance" class="connection-marker">{{ statusLabel[port.assistance.status as keyof typeof statusLabel] }}</small><small v-else-if="port.reception?.subscriptions.length" :title="port.reception.subscriptions.map((sub: any) => sub.from || sub.from_topic).join(', ')">{{ port.reception.subscriptions.length }} link(s) · {{ port.reception.conflict ? 'QoS conflict' : port.reception.policy.buffer }}</small></div>
      <div v-for="port in data.outputs" :key="'o' + port.endpoint" class="node-port output" :data-connection-status="port.assistance?.status" :title="port.assistance?.message" @pointerenter="port.assistance && emit('port-hover', port.endpoint + ' · ' + port.assistance.message)"><span>{{ port.name }}</span><code>{{ port.root }}</code><small v-if="port.assistance" class="connection-marker">{{ statusLabel[port.assistance.status as keyof typeof statusLabel] }}</small><Handle :id="port.endpoint" type="source" :position="Position.Right" /></div>
    </div>
  </div>
</template>

<style scoped>
.node-port[data-connection-status=compatible] { background: #eaf7f0; outline: 1px solid #3a926a; }
.node-port[data-connection-status=incompatible] { background: #fff0ef; color: #9b3535; }
.node-port[data-connection-status=unknown] { background: #fff8e6; outline: 1px dashed #b68a2e; }
.connection-marker { max-width: 86px; flex-shrink: 0; font-weight: 600; }
</style>
