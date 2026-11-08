<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { useProject } from '../store'
import { keysFor, choices, nodeLabel } from '../hints'
import { parameterShape } from '../schema'
const props = defineProps<{ node: any; file: string; role: string; depth?: number; readonly?: boolean }>()
const store = useProject(), page = ref(0), key = ref(''), literal = ref('null'), showAdd = ref(false), rename = ref(false), newName = ref('')
const value = ref(''), type = ref('string')
const expanded = ref(new Set<string>((props.depth || 0) < 1 ? props.node.children.map((n: any) => n.name) : []))
const collection = computed(() => ['array', 'object'].includes(props.node.kind))
const children = computed(() => props.node.children.slice(page.value * 40, (page.value + 1) * 40))
const shape = computed(() => parameterShape(store.project, props.node.path))
const hints = computed(() => ({ ...keysFor(props.role, props.node.path), ...shape.value?.suggestions }))
const suggestions = computed(() => Object.keys(hints.value).filter(k => !props.node.children.some((n: any) => n.name === k)))
watch(() => [props.node.literal, props.node.kind], () => {
  type.value = props.node.kind
  if (!collection.value) { try { value.value = props.node.kind === 'string' ? JSON.parse(props.node.literal) : props.node.literal } catch { value.value = props.node.literal } }
}, { immediate: true })
watch(key, () => { literal.value = hints.value[key.value] || 'null' })
function commit() {
  const text = type.value === 'string' ? JSON.stringify(value.value) : type.value === 'null' ? 'null' : value.value
  store.command({ action: 'set', file: props.file, path: props.node.path, literal: text })
}
function add() { store.command({ action: 'set', file: props.file, path: [...props.node.path, props.node.kind === 'array' ? '-' : key.value], literal: literal.value }); showAdd.value = false }
</script>
<template>
  <div class="json-node" :class="{ collection }">
    <small v-if="shape" class="type-tag">SDL {{ shape.type }}{{ shape.dimensions.map((d: any) => '[' + d + ']').join('') }} · {{ shape.modifier }}</small>
    <template v-if="collection">
      <div class="collection-tools">
        <span class="type-tag">{{ node.kind }} · {{ node.children.length }}</span>
        <Button v-if="!readonly" label="Add" size="small" severity="secondary" @click="showAdd = !showAdd" />
      </div>
      <div v-if="showAdd" class="add-form">
        <label v-if="node.kind === 'object'">Property name<InputText v-model="key" list="suggested-properties" /><datalist id="suggested-properties"><option v-for="item in suggestions" :key="item">{{ item }}</option></datalist></label>
        <label>Initial JSON value<textarea v-model="literal" rows="3" spellcheck="false" /></label>
        <Button label="Preview addition" :disabled="node.kind === 'object' && !key" @click="add" />
      </div>
      <details v-for="child in children" :key="child.name" :open="expanded.has(child.name)" class="property-branch" @toggle="event => { if ((event.target as HTMLDetailsElement).open) expanded.add(child.name); else expanded.delete(child.name) }">
        <summary><span>{{ nodeLabel(child) }}</span><small>{{ child.kind }}</small>
          <button v-if="!readonly" title="Select this property" aria-label="Inspect property" @click.stop.prevent="store.select(file, child.path)">↗</button>
        </summary>
        <JsonTree v-if="expanded.has(child.name)" :node="child" :file="file" :role="role" :depth="(depth || 0) + 1" :readonly="readonly" />
        <Button v-if="!readonly" label="Remove property" size="small" severity="danger" variant="text" @click="store.command({ action: 'delete', file, path: child.path })" />
      </details>
      <div v-if="node.children.length > 40" class="pager"><Button label="Previous" :disabled="page === 0" @click="page--" /><span>{{ page + 1 }} / {{ Math.ceil(node.children.length / 40) }}</span><Button label="Next" :disabled="(page + 1) * 40 >= node.children.length" @click="page++" /></div>
    </template>
    <div v-else class="scalar-editor">
      <Select v-model="type" :options="['string', 'number', 'boolean', 'null', 'object', 'array']" aria-label="Value type" :disabled="readonly" />
      <Select v-if="type === 'boolean'" v-model="value" :options="['true', 'false']" aria-label="Boolean value" :disabled="readonly" />
      <Select v-else-if="type === 'string' && (choices[node.name] || shape?.enums)" v-model="value" :options="shape?.enums || choices[node.name]" editable aria-label="Property value" :disabled="readonly" />
      <InputText v-else-if="type !== 'null'" v-model="value" :type="node.name === 'token' ? 'password' : 'text'" :aria-label="node.name || 'Value'" :disabled="readonly" />
      <Button v-if="!readonly" label="Preview change" size="small" @click="commit" />
      <small v-if="type === 'number'">Exact numeric literal; no JavaScript rounding.</small>
    </div>
  </div>
</template>
