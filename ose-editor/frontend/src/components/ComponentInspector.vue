<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import Button from 'primevue/button'
import { useProject } from '../store'
import { inputEvents } from '../execution'
import { rootTypes, selectedActivation } from '../component-editing'
import ExecutionView from './ExecutionView.vue'
import ReceptionView from './ReceptionView.vue'
import ContractView from './ContractView.vue'
import InspectorSection from './InspectorSection.vue'
const store = useProject(), typesId = useId()
const model = computed(() => store.project.documents[store.project.model].value)
const name = computed(() => store.editingComponent)
const node = computed(() => model.value.components?.[name.value])
const path = computed(() => store.selection?.file === store.project.model ? store.selection?.path || [] : [])
const direction = computed(() => path.value[1] === name.value && ['inputs', 'outputs'].includes(path.value[2]) ? path.value[2] : '')
const port = computed(() => direction.value ? path.value[3] || '' : '')
const link = computed(() => selectedActivation(store))
const disabled = computed(() => !!store.pending || store.busy || store.project.documents[store.project.model].stale)
const creation = computed(() => store.portCreation?.kind)
const roots = computed(() => rootTypes(store.project))
const task = ref(''), newName = ref(''), outputName = ref(''), root = ref(''), schema = ref('')
const activation = ref({ input: '', event: 'message', output: '' })
watch(() => [name.value, store.selection, store.project.revision, store.portCreation], () => {
  task.value = ''; newName.value = port.value || name.value
  const desc = direction.value === 'outputs' ? node.value?.outputs?.[port.value] : null
  outputName.value = desc ? port.value : ''; root.value = desc?.root || ''; schema.value = desc?.schema || ''
  activation.value = link.value ? { ...link.value } : { input: '', event: 'message', output: '' }
}, { immediate: true })
const linkChanged = computed(() => link.value && Object.entries(activation.value).some(([key, value]) => (link.value as any)[key] !== value))
const activationInputs = computed(() => Object.keys(node.value?.inputs || {}).filter(input => inputEvents(node.value.inputs[input]).length))
const events = computed(() => inputEvents(node.value?.inputs?.[activation.value.input]))
function eventChanged() { if (!events.value.includes(activation.value.event as any)) activation.value.event = events.value[0] || 'message' }
const subscriptions = computed(() => (model.value.subscriptions || []).flatMap((sub: any, index: number) => {
  const endpoint = name.value + '.' + port.value
  return port.value && (direction.value === 'inputs' ? sub.to === endpoint : sub.from === endpoint) ? [{ ...sub, index }] : []
}))
function rename() {
  if (disabled.value || !newName.value.trim() || newName.value === (port.value || name.value)) return
  store.command({ action: 'rename', kind: port.value ? 'port' : 'component', old: port.value ? name.value + '.' + port.value : name.value,
    ...(port.value ? { direction: direction.value } : {}), name: newName.value.trim() })
}
function duplicate() {
  if (!disabled.value && newName.value.trim()) store.command({ action: 'duplicate_port', component: name.value, direction: direction.value, source: port.value, port: newName.value.trim() })
}
function duplicateName() {
  let candidate = port.value + '_copy', index = 2
  while (node.value[direction.value]?.[candidate]) candidate = port.value + '_copy' + index++
  task.value = 'duplicate'; newName.value = candidate
}
function remove() { if (!disabled.value) store.command({ action: 'remove_port', component: name.value, direction: direction.value, port: port.value }) }
function saveOutput() {
  if (disabled.value || !root.value.trim() || !(creation.value === 'output' ? outputName.value.trim() : port.value)) return
  store.command({ action: 'configure_output', component: name.value, port: creation.value === 'output' ? outputName.value.trim() : port.value,
    ...(creation.value === 'output' ? { create: true } : {}), description: { root: root.value.trim(), ...(schema.value.trim() ? { schema: schema.value.trim() } : {}) } })
}
function saveLink() {
  if (!disabled.value && linkChanged.value && link.value && activation.value.input && activation.value.output)
    store.command({ action: 'configure_activation', component: name.value, ...activation.value, previous: { ...link.value }, enabled: true })
}
function removeLink() { if (!disabled.value && link.value) store.command({ action: 'configure_activation', component: name.value, ...link.value, enabled: false }) }
</script>
<template>
  <section v-if="node" class="component-properties" aria-label="Component element properties">
    <p class="inspector-location">{{ name }}</p><p v-if="store.project.documents[store.project.model].stale" class="error">Repair the model source before applying structured edits.</p>
    <template v-if="path[0] === 'subscriptions'"><h3>Connection</h3><Button label="Component properties" size="small" severity="secondary" @click="store.select(store.project.model, ['components', name])" /><InspectorSection v-if="node.inputs?.[model.subscriptions?.[Number(path[1])]?.to?.split('.')[1]]?.kind !== 'timer'" title="Reception"><ReceptionView /></InspectorSection><InspectorSection title="Contract"><ContractView /></InspectorSection></template>
    <template v-else-if="link">
      <h3>Activation link</h3><InspectorSection title="General"><form @submit.prevent="saveLink"><fieldset :disabled="disabled">
        <label>Input<select v-model="activation.input" aria-label="Activation input" @change="eventChanged"><option v-for="input in activationInputs" :key="input">{{ input }}</option></select></label>
        <label>Event<select v-model="activation.event" aria-label="Activation event"><option v-for="event in events" :key="event">{{ event }}</option></select></label>
        <label>Output<select v-model="activation.output" aria-label="Activation output"><option v-for="output in Object.keys(node.outputs)" :key="output">{{ output }}</option></select></label>
        <Button type="submit" label="Preview link changes" size="small" :disabled="!linkChanged" />
      </fieldset></form></InspectorSection><InspectorSection title="Advanced" :initial-open="false"><div class="danger-actions"><Button label="Remove activation link" :disabled="disabled" severity="danger" size="small" @click="removeLink" /></div></InspectorSection>
    </template>
    <template v-else-if="port || creation">
      <h3>{{ creation ? 'New ' + (creation === 'message' ? 'input' : creation) : port }}</h3>
      <InspectorSection v-if="port || creation === 'output'" title="General"><fieldset v-if="port && !creation" :disabled="disabled" class="port-actions"><Button label="Rename" size="small" severity="secondary" @click="task = 'rename'; newName = port" /><Button label="Duplicate" size="small" severity="secondary" @click="duplicateName" /></fieldset>
      <form v-if="task" @submit.prevent="task === 'rename' ? rename() : duplicate()"><fieldset :disabled="disabled"><label>New name<input v-model.trim="newName" aria-label="New port name" required /></label><Button type="submit" :label="task === 'rename' ? 'Preview rename' : 'Preview duplicate'" size="small" /></fieldset></form>
      <template v-if="direction === 'outputs' && !creation || creation === 'output'">
        <form aria-label="Configure output" @submit.prevent="saveOutput"><fieldset :disabled="disabled"><label v-if="creation">Output name<input v-model.trim="outputName" aria-label="New output name" required /></label><label>Logical root<input v-model="root" :list="typesId" aria-label="Output logical root" required /></label><label>Schema override<input v-model="schema" aria-label="Output schema override" placeholder="Use the model schema" /></label><Button type="submit" label="Preview output changes" size="small" :disabled="!root.trim() || !!creation && !outputName.trim()" /></fieldset></form><datalist :id="typesId"><option v-for="type in roots" :key="type" :value="type" /></datalist>
      </template>
      </InspectorSection><template v-if="!(direction === 'outputs' && !creation || creation === 'output')"><InspectorSection title="Execution"><ExecutionView compact /></InspectorSection><InspectorSection v-if="!creation && node.inputs?.[port]?.kind !== 'timer'" title="Reception"><ReceptionView /></InspectorSection></template>
      <InspectorSection v-if="direction === 'outputs' && !creation" title="Contract"><ContractView /></InspectorSection><section v-if="subscriptions.length && !creation && direction === 'outputs'" class="port-connections"><h4>External connections</h4><button v-for="sub in subscriptions" :key="sub.id" @click="store.select(store.project.model, ['subscriptions', String(sub.index)])">{{ sub.id }} · {{ sub.to }}</button></section>
      <InspectorSection v-if="port && !creation" title="Advanced" :initial-open="false"><div class="danger-actions"><Button label="Remove port" :disabled="disabled" size="small" severity="danger" @click="remove" /></div><p class="help">Renames update references. Removing a port includes its subscriptions and activation permissions in the preview.</p></InspectorSection>
    </template>
    <template v-else>
      <h3>Component properties</h3><InspectorSection title="General"><form @submit.prevent="rename"><fieldset :disabled="disabled"><label>Component name<input v-model.trim="newName" aria-label="Component name" required /></label><Button type="submit" label="Preview component rename" size="small" :disabled="newName === name || !newName" /></fieldset></form><p class="help">{{ Object.keys(node.inputs).length }} input(s) · {{ Object.keys(node.outputs).length }} output(s)</p><p class="help">Select a port or an activation link on the canvas to edit its properties.</p></InspectorSection>
    </template>
  </section>
</template>
<style scoped>
.component-properties { display: grid; gap: 14px; }.component-properties h3 { font-size: 14px; }.component-properties form > fieldset { border: 0; padding: 0; margin: 0; display: grid; gap: 12px; min-width: 0; }.component-properties label { display: grid; gap: 5px; font-size: 12px; }.component-properties input, .component-properties select { width: 100%; padding: 8px; border: 1px solid #cbd5e1; border-radius: 5px; background: white; color: #1c3049; }.port-actions { border: 0; padding: 0; margin: 0; display: flex; gap: 5px; flex-wrap: wrap; }.help { font-size: 11px; line-height: 1.6; color: #6e819b; }.port-connections { display: grid; gap: 8px; }.port-connections button { text-align: left; background: none; border: 0; padding: 0; color: #3159a4; overflow-wrap: anywhere; }.port-connections h4 { font-size: 12px; }
</style>
