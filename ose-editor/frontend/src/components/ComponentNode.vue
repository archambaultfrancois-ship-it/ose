<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
defineProps<{ data: any; selected?: boolean }>()
</script>
<template>
  <div class="component-node" :class="[data.kind, { selected }]">
    <header><span class="node-icon">{{ data.kind === 'external' ? '↗' : data.kind === 'group' ? '▣' : '◇' }}</span><strong>{{ data.name }}</strong><small>{{ data.kind }}</small></header>
    <div class="node-trigger">{{ data.trigger?.mode === 'periodic' ? '◷ ' + (data.trigger.period_ms?.$number || data.trigger.period_ms) + ' ms' : data.kind === 'group' ? 'Visual group · double-click to expand' : data.kind === 'external' ? 'External publisher' : '↯ on change' }}</div>
    <div class="node-ports">
      <div v-for="port in data.inputs" :key="'i' + port.endpoint" class="node-port input"><Handle :id="port.endpoint" type="target" :position="Position.Left" /><span>{{ port.name }}<small v-if="!port.required"> optional</small></span><code>{{ port.root }}</code></div>
      <div v-for="port in data.outputs" :key="'o' + port.endpoint" class="node-port output"><span>{{ port.name }}</span><code>{{ port.root }}</code><Handle :id="port.endpoint" type="source" :position="Position.Right" /></div>
    </div>
  </div>
</template>
