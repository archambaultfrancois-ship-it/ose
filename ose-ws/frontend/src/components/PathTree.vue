<script setup lang="ts">
defineProps<{ nodes: any[]; mode: 'measurements' | 'parameters'; checked: string[]; active?: string }>()
const emit = defineEmits<{ select: [item: any]; check: [id: string, checked: boolean] }>()
</script>
<template>
  <div v-for="node in nodes" :key="node.label" class="path-node">
    <label v-if="node.item && mode === 'measurements'" class="measurement-option"><input type="checkbox" :checked="checked.includes(node.item.id)" @change="emit('check', node.item.id, ($event.target as HTMLInputElement).checked)" /><span><code>{{ node.label }}</code><small>{{ node.item.value ?? '—' }} · {{ node.item.quality }}</small></span></label>
    <div v-if="node.item && mode === 'parameters'" class="block-option" :class="{ active: active === node.item.id }"><input v-if="node.item.permissions.read" type="checkbox" :checked="checked.includes(node.item.id)" :aria-label="`Select ${node.item.path} for export`" @change="emit('check', node.item.id, ($event.target as HTMLInputElement).checked)" /><button :disabled="!node.item.permissions.read" @click="emit('select', node.item)"><span class="path">{{ node.label }}</span><small>{{ node.item.permissions.read ? node.item.permissions.write ? 'Read and write · web policy' : 'Read only · web policy' : 'Write only · editor unavailable' }}</small></button></div>
    <details v-if="node.children.length" open><summary>{{ node.label }}</summary><div class="path-children"><PathTree :nodes="node.children" :mode="mode" :checked="checked" :active="active" @select="emit('select', $event)" @check="(id, checked) => emit('check', id, checked)" /></div></details>
  </div>
</template>
