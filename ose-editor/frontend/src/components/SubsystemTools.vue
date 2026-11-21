<script setup lang="ts">
import { computed, ref } from 'vue'
import Button from 'primevue/button'
import { useProject } from '../store'
import { graphModel } from '../subsystems'
const props = defineProps<{ selected: string[] }>()
const store = useProject(), action = ref(''), instance = ref(''), source = ref(''), directory = ref('')
const direction = ref('inputs'), target = ref(''), alias = ref('')
const definition = computed(() => store.project.model_contexts?.[store.project.model]?.definitions?.[0])
const targets = computed(() => {
  const model = graphModel(store.project)
  if (direction.value === 'parameters') return Object.keys(model?.shared_parameters || {})
  return Object.entries(model?.components || {}).flatMap(([name, value]: any) => Object.entries(value[direction.value] || {})
    .filter(([, desc]: any) => desc.kind !== 'timer').map(([port]) => name + '.' + port))
})
function open(value: string) { action.value = value; instance.value = ''; source.value = ''; directory.value = ''; target.value = ''; alias.value = '' }
async function preview() {
  const command = action.value === 'expose_subsystem_port'
    ? { action: action.value, definition: definition.value, direction: direction.value, target: target.value, name: alias.value }
    : { action: action.value, name: instance.value, source: source.value, directory: directory.value, components: props.selected }
  if (await store.command(command)) action.value = ''
}
</script>
<template>
  <div class="subsystem-tools">
    <div class="actions">
      <label v-if="definition">Definition deployment<select :value="store.project.selected_deployment" aria-label="Definition deployment" @change="store.command({ action: 'select_deployment', model: store.project.model, file: ($event.target as HTMLSelectElement).value }, false)"><option v-for="file in store.project.deployments" :key="file">{{ file }}</option></select></label>
      <Button label="Instantiate subsystem" size="small" severity="secondary" :disabled="!!store.pending" @click="open('instantiate_subsystem')" />
      <Button label="New subsystem" size="small" severity="secondary" :disabled="!!store.pending" @click="open('create_subsystem')" />
      <Button label="Extract selection" size="small" severity="secondary" :disabled="!selected.length || !!store.pending" @click="open('extract_subsystem')" />
      <Button v-if="definition" label="Expose public port" size="small" severity="secondary" :disabled="!!store.pending" @click="open('expose_subsystem_port')" />
    </div>
    <form v-if="action" aria-label="Subsystem operation" @submit.prevent="preview" @keydown.esc="action = ''">
      <template v-if="action === 'expose_subsystem_port'">
        <label>Direction<select v-model="direction" aria-label="Public interface direction" @change="target = ''"><option>inputs</option><option>outputs</option><option>parameters</option></select></label>
        <label>Internal target<select v-model="target" aria-label="Public interface target" required><option value="" disabled>Choose a target</option><option v-for="endpoint in targets" :key="endpoint">{{ endpoint }}</option></select></label>
        <label>Public name<input v-model="alias" aria-label="Public interface name" required pattern="[A-Za-z][A-Za-z0-9_]*" /></label>
      </template>
      <template v-else>
        <label>Instance name<input v-model="instance" aria-label="Subsystem instance name" required pattern="[A-Za-z][A-Za-z0-9_]*" /></label>
        <label v-if="action === 'instantiate_subsystem'">Definition path<input v-model="source" aria-label="Subsystem definition path" placeholder="../detection/subsystem.json" required /></label>
        <label v-else>New directory<input v-model="directory" aria-label="Subsystem directory" placeholder="subsystems/processing" required /></label>
      </template>
      <p v-if="action === 'extract_subsystem'">Extract {{ selected.join(', ') }}. The preview includes the public interface, internal connections and registered deployments. Handler files stay at their current locations.</p>
      <p v-if="action === 'create_subsystem'">Create an empty definition and its model/deployment drafts, then open the definition to add components.</p>
      <div class="actions"><Button type="submit" label="Preview subsystem changes" :disabled="store.busy || !!store.pending" /><Button type="button" label="Cancel" severity="secondary" @click="action = ''" /></div>
    </form>
  </div>
</template>
<style scoped>
.subsystem-tools { padding: 8px 16px; border-bottom: 1px solid #dce4ef; }
.actions, form { display: flex; flex-wrap: wrap; gap: 8px; align-items: end; }
form { padding-top: 12px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
input, select { padding: 7px; border: 1px solid #bcc9dd; border-radius: 5px; }
p { width: 100%; font-size: 12px; }
</style>
