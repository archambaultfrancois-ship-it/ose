<script setup lang="ts">
import { computed, ref } from 'vue'
import { defaultValue, inputValue, presentationAt } from '../fields.mjs'
const props = withDefaults(defineProps<{
  schema: any; type: string; modelValue: any; label: string; dimensions?: number[];
  cardinality?: string; readonly?: boolean; pointer?: string; depth?: number
}>(), { dimensions: () => [], cardinality: 'required', readonly: false, pointer: '', depth: 0 })
const emit = defineEmits<{ 'update:modelValue': [value: any] }>()
const offset = ref(0)
const fieldError = ref('')
const shape = computed(() => props.schema.types[props.type])
const array = computed(() => props.dimensions.length > 0 || ['repeated', 'packed'].includes(props.cardinality))
const sequence = computed(() => ['repeated', 'packed'].includes(props.cardinality))
const childrenDimensions = computed(() => sequence.value ? props.dimensions : props.dimensions.slice(1))
const values = computed<any[]>(() => Array.isArray(props.modelValue) ? props.modelValue : [])
const visible = computed(() => values.value.slice(offset.value, offset.value + 40))
const metadata = computed<any>(() => presentationAt(props.schema, props.pointer))
function updateChild(key: string | number, value: any) {
  const result = array.value ? [...values.value] : { ...props.modelValue }
  result[key] = value
  emit('update:modelValue', result)
}
function present(event: Event) {
  try {
    fieldError.value = ''
    emit('update:modelValue', (event.target as HTMLInputElement).checked
      ? defaultValue(props.schema, props.type, props.dimensions) : null)
  } catch (error: any) { fieldError.value = error.message; (event.target as HTMLInputElement).checked = false }
}
function append() {
  try { fieldError.value = ''; emit('update:modelValue', [...values.value, defaultValue(props.schema, props.type, props.dimensions)]) }
  catch (error: any) { fieldError.value = error.message }
}
function remove(index: number) { emit('update:modelValue', values.value.filter((_, i) => i !== index)) }
function move(index: number) {
  if (index <= 0) return
  const result = [...values.value]
  ;[result[index - 1], result[index]] = [result[index], result[index - 1]]
  emit('update:modelValue', result)
}
</script>

<template>
  <div class="schema-field" :class="{ nested: depth > 0 }">
    <div class="field-label">
      <strong>{{ metadata.label || label }}</strong><code>{{ type }}{{ dimensions.map(n => `[${n}]`).join('') }}</code>
      <span v-if="metadata.unit" class="muted">{{ metadata.unit }}</span>
      <label v-if="cardinality === 'optional'" class="inline"><input type="checkbox" :checked="modelValue != null" :disabled="readonly" @change="present" /> Present</label>
    </div>
    <p v-if="metadata.help" class="muted small">{{ metadata.help }}</p>
    <p v-if="fieldError" class="notice error" role="alert">{{ fieldError }}</p>
    <span v-if="cardinality === 'optional' && modelValue == null" class="muted">Absent</span>
    <span v-else-if="depth >= 48" class="muted">Nesting limit reached. Use a bounded import for deeper structures.</span>
    <details v-else-if="array" :open="depth < 2">
      <summary>{{ values.length }} elements{{ sequence ? ' · editable sequence' : ' · fixed dimensions' }}</summary>
      <div v-for="(item, i) in visible" :key="i + offset" class="array-item">
        <SchemaField :schema="schema" :type="type" :model-value="item" :label="`[${i + offset}]`" :dimensions="childrenDimensions" :readonly="readonly" :pointer="`${pointer}/${i + offset}`" :depth="depth + 1" @update:model-value="updateChild(i + offset, $event)" />
        <div v-if="sequence && !readonly" class="actions compact"><button class="quiet" :disabled="i + offset === 0" @click="move(i + offset)">Move up</button><button class="quiet danger" @click="remove(i + offset)">Remove</button></div>
      </div>
      <div v-if="values.length > 40" class="actions compact"><button :disabled="offset === 0" @click="offset = Math.max(0, offset - 40)">Previous 40</button><span>{{ offset + 1 }}–{{ Math.min(offset + 40, values.length) }}</span><button :disabled="offset + 40 >= values.length" @click="offset += 40">Next 40</button></div>
      <button v-if="sequence && !readonly" class="quiet" @click="append">+ Add element</button>
    </details>
    <details v-else-if="shape?.kind === 'message'" :open="depth < 2">
      <summary>{{ shape.fields.length }} fields</summary>
      <SchemaField v-for="field in shape.fields" :key="field.id" :schema="schema" :type="field.type" :model-value="modelValue?.[field.name]" :label="field.name" :dimensions="field.dimensions" :cardinality="field.cardinality" :readonly="readonly" :pointer="`${pointer}/${field.name.replaceAll('~', '~0').replaceAll('/', '~1')}`" :depth="depth + 1" @update:model-value="updateChild(field.name, $event)" />
    </details>
    <select v-else-if="shape?.kind === 'enum'" :value="modelValue" :disabled="readonly" :aria-label="label" @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)">
      <option v-for="item in shape.values" :key="item.name" :value="item.name">{{ item.name }} ({{ item.value }})</option>
    </select>
    <label v-else-if="type === 'bool'" class="inline"><input type="checkbox" :checked="modelValue" :disabled="readonly" @change="emit('update:modelValue', ($event.target as HTMLInputElement).checked)" /> {{ modelValue ? 'True' : 'False' }}</label>
    <div v-else-if="type === 'c32' || type === 'c64'" class="complex">
      <label>Real<input :value="modelValue?.real" :readonly="readonly" inputmode="decimal" @input="updateChild('real', inputValue('fl64', ($event.target as HTMLInputElement).value))" /></label>
      <label>Imaginary<input :value="modelValue?.imag" :readonly="readonly" inputmode="decimal" @input="updateChild('imag', inputValue('fl64', ($event.target as HTMLInputElement).value))" /></label>
    </div>
    <textarea v-else-if="type === 'string' && metadata.widget === 'textarea'" :value="modelValue" :readonly="readonly" :aria-label="label" @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)" />
    <input v-else-if="['string', 'int8', 'uint8', 'int16', 'uint16', 'int32', 'uint32', 'int64', 'uint64', 'fl32', 'fl64'].includes(type)" :value="modelValue" :readonly="readonly" :aria-label="label" :inputmode="type === 'string' ? 'text' : type.startsWith('fl') ? 'decimal' : 'numeric'" :spellcheck="false" @input="emit('update:modelValue', inputValue(type, ($event.target as HTMLInputElement).value))" />
    <span v-else class="warning">Unsupported type: {{ type }}. This field cannot be edited.</span>
  </div>
</template>
