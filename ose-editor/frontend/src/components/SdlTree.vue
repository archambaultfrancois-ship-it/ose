<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { useProject } from '../store'
const props = defineProps<{ file: string; nodes: any[]; selected?: string; readonly?: boolean }>()
const store = useProject(), kind = ref('message'), name = ref(''), initial = ref(''), addParent = ref(''), showAdd = ref(false)
const expanded = ref(new Set<string>())
watch(() => props.selected, selected => { if (selected) props.nodes.forEach(node => { if (selected === node.path || selected.startsWith(node.path + '/')) expanded.value.add(node.path) }) }, { immediate: true })
const kinds = computed(() => !addParent.value ? ['message', 'enum', 'const', 'alias', 'package'] : ['field', 'anonymous', 'item'])
function edit(node: any, property: string | number, value: string) {
  if (property === 'name' && ['message', 'enum', 'alias', 'const', 'package'].includes(node.kind)) store.command({ action: 'rename', kind: 'sdl', file: props.file, path: node.path, old: node.name, name: value })
  else store.command({ action: 'sdl_edit', file: props.file, path: node.path, property, value })
}
function add(parent = '') { addParent.value = parent; kind.value = parent ? 'field' : 'message'; showAdd.value = true }
function submit() { store.command({ action: 'sdl_add', file: props.file, path: addParent.value, kind: kind.value, name: name.value, value: initial.value }); showAdd.value = false }
function wireValue(node: any, key: string) { return node.properties.wire.value.split(',').map((part: string) => part.split('=').map(s => s.trim())).find((pair: string[]) => pair[0] === key)?.[1] || '' }
function wire(node: any, key: string, value: string) { const pairs = node.properties.wire.value.split(',').filter((part: string) => part.trim()).map((part: string) => part.split('=').map(s => s.trim())); const index = pairs.findIndex((pair: string[]) => pair[0] === key); if (index >= 0) { if (value) pairs[index][1] = value; else pairs.splice(index, 1) } else if (value) pairs.push([key, value]); edit(node, 'wire', pairs.map((pair: string[]) => pair.join('=')).join(',')) }
function resolved(node: any) { const result = store.project?.validation; if (result?.revision !== store.project?.revision) return null; for (const [input, files] of Object.entries(store.project.schema_inputs)) { if (!(files as string[]).includes(props.file)) continue; for (const unit of result.resolved?.[input] || []) { const table = node.kind === 'const' ? unit.constants : node.kind === 'alias' ? unit.aliases : null; if (table) { const entry = table[node.name] || table[(unit.package ? unit.package + '.' : '') + node.name]; if (entry) return entry } } } return null }
</script>
<template>
  <div class="sdl-tree">
    <div v-if="!readonly && (!nodes.length || nodes[0].path.split('/').length === 2)" class="actions"><Button label="Add declaration" size="small" severity="secondary" @click="add()" /><Button label="Make IDs explicit" size="small" severity="secondary" @click="store.command({ action: 'sdl_stabilize_ids', file })" /></div>
    <div v-if="showAdd" class="add-form"><Select v-model="kind" :options="kinds" aria-label="Declaration kind" /><InputText v-model="name" placeholder="Name" aria-label="New declaration name" /><InputText v-model="initial" placeholder="Type, value or expression" aria-label="Initial declaration value" /><Button label="Preview addition" :disabled="!name" @click="submit" /></div>
    <details v-for="node in nodes" :key="node.path" :open="expanded.has(node.path)" class="property-branch" @toggle="event => { if ((event.target as HTMLDetailsElement).open) expanded.add(node.path); else expanded.delete(node.path) }">
      <summary @click.stop="store.select(file, undefined, node.path)"><span>{{ node.name }}</span><small>{{ node.kind }}{{ node.effective_id ? ' · #' + node.effective_id : '' }}</small></summary>
      <div class="sdl-properties" v-if="selected === node.path">
        <label v-for="(property, key) in node.properties" :key="key">{{ key }}
          <Select v-if="property.choices" :model-value="property.value" :options="property.choices" :disabled="readonly" :aria-label="String(key)" @update:model-value="value => store.command({ action: 'sdl_edit', file, path: node.path, property: key, value })" />
          <textarea v-else-if="key === 'doc'" :value="property.value" :disabled="readonly" :aria-label="String(key)" @change="event => store.command({ action: 'sdl_edit', file, path: node.path, property: key, value: (event.target as HTMLTextAreaElement).value })" />
          <InputText v-else :model-value="property.value" :disabled="readonly" :aria-label="String(key)" @change="event => edit(node, key, (event.target as HTMLInputElement).value)" />
        </label>
        <small v-if="node.kind === 'field'">ID {{ node.effective_id }} · {{ node.properties.modifier?.value || 'required (implicit)' }}</small>
        <fieldset v-if="node.properties.wire" class="wire-controls"><legend>Wire representation</legend><label v-for="key in ['bits', 'q', 'encoding', 'align', 'pad_after']" :key="key">{{ key }}<InputText :model-value="wireValue(node, key)" :disabled="readonly" :aria-label="'Wire ' + key" placeholder="omitted" @change="event => wire(node, key, (event.target as HTMLInputElement).value)" /></label></fieldset>
        <div v-if="resolved(node)" class="resolved-value"><small>Resolved by SDL</small><pre>{{ JSON.stringify(resolved(node), null, 2) }}</pre></div>
        <div class="actions"><Button v-if="node.body_end !== undefined" label="Add child" size="small" severity="secondary" @click="add(node.path)" /><Button label="Delete declaration" size="small" severity="danger" @click="store.command({ action: 'sdl_delete', file, path: node.path })" /></div>
      </div>
      <SdlTree v-if="node.children.length && expanded.has(node.path)" :nodes="node.children" :file="file" :selected="selected" :readonly="readonly" />
    </details>
  </div>
</template>
