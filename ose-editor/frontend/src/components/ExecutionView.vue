<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import Button from 'primevue/button'
import { useProject } from '../store'
import { scalarText } from '../hints'
import { rootTypes } from '../component-editing'
import { activationCycle, inputKind } from '../execution'
const props = defineProps<{ compact?: boolean }>()
const store = useProject(), typesId = useId()
const roots = computed(() => rootTypes(store.project))
const model = computed(() => store.project?.documents?.[store.project.model]?.value)
const componentName = computed(() => {
  if (store.selection?.file !== store.project?.model) return ''
  const path = store.selection?.path || []
  if (path[0] === 'components' && path.length >= 2) return path[1]
  if (path[0] === 'subscriptions' && path.length >= 2) return model.value?.subscriptions?.[Number(path[1])]?.to?.split('.')[0] || ''
  return ''
})
const component = computed(() => model.value?.components?.[componentName.value])
const selectedPort = computed(() => {
  const path = store.selection?.path || []
  return path[2] === 'inputs' && path.length >= 4 ? path[3]
    : path[0] === 'subscriptions' ? model.value?.subscriptions?.[Number(path[1])]?.to?.split('.')[1] : ''
})
const port = ref(''), creating = ref(false)
const draft = ref({ kind: 'message', mode: 'push', root: '', schema: '', timeoutEnabled: false, timeout: '0.5',
  timerMode: 'periodic', delay: '0.1', start: 'automatic', messageOutputs: [] as string[], timeoutOutputs: [] as string[] })
function load(name: string) {
  port.value = name; creating.value = false
  const desc = component.value?.inputs?.[name] || {}
  draft.value = { kind: inputKind(desc), mode: desc.mode || 'push', root: desc.root || '', schema: desc.schema || '',
    timeoutEnabled: desc.timeout !== undefined, timeout: desc.timeout !== undefined ? scalarText(desc.timeout) : '0.5',
    timerMode: desc.timer?.mode || 'periodic', delay: scalarText(desc.timer?.delay ?? '0.1'), start: desc.timer?.start || 'automatic',
    messageOutputs: Array.isArray(desc.activates?.message) ? [...desc.activates.message] : [],
    timeoutOutputs: Array.isArray(desc.activates?.timeout) ? [...desc.activates.timeout] : [] }
}
watch(() => [componentName.value, selectedPort.value, store.project?.revision, store.portCreation], () => {
  const creation = store.portCreation
  if (props.compact && creation && creation.kind !== 'output') {
    newInput(); draft.value.kind = creation.kind; return
  }
  const chosen = selectedPort.value || (component.value?.inputs?.[port.value] ? port.value : Object.keys(component.value?.inputs || {})[0] || '')
  load(chosen)
}, { immediate: true })
function newInput() { load(''); creating.value = true }
const incoming = computed(() => (model.value?.subscriptions || []).filter((sub: any) => sub.to === componentName.value + '.' + port.value))
const events = computed(() => draft.value.kind === 'timer' ? ['timeout'] : draft.value.mode === 'pull' ? [] : draft.value.timeoutEnabled ? ['message', 'timeout'] : ['message'])
const outputs = computed(() => [...new Set([...Object.keys(component.value?.outputs || {}), ...draft.value.messageOutputs, ...draft.value.timeoutOutputs])])
function seconds(value: string) {
  const result = Number(value)
  if (!value.trim() || !Number.isFinite(result) || result <= 0 || Math.round(result * 1e9) < 1) throw new Error('Delays must be positive seconds, at least one nanosecond.')
  return result
}
function description() {
  const value = draft.value, activates: Record<string, string[]> = {}
  if (events.value.includes('message')) activates.message = [...value.messageOutputs]
  if (events.value.includes('timeout')) activates.timeout = [...value.timeoutOutputs]
  if (value.kind === 'timer') return { kind: 'timer', timer: { mode: value.timerMode, delay: seconds(value.delay), start: value.start }, activates }
  if (!value.root.trim()) throw new Error('A message input needs a logical root type.')
  return { root: value.root.trim(), mode: value.mode, ...(value.schema.trim() ? { schema: value.schema.trim() } : {}),
    ...(value.mode === 'push' ? { ...(value.timeoutEnabled ? { timeout: seconds(value.timeout) } : {}), activates } : {}) }
}
const problem = computed(() => {
  if (!component.value || !port.value) return 'Choose an input name.'
  if (creating.value && component.value.inputs?.[port.value]) return 'This input already exists.'
  if (draft.value.kind === 'timer' && incoming.value.length) return 'Disconnect incoming subscriptions before converting this input to a timer.'
  try {
    description()
    return ''
  } catch (failure) { return (failure as Error).message }
})
const cycleWarning = computed(() => {
  if (!component.value || !port.value || problem.value) return ''
  const proposed = { ...model.value, components: { ...model.value.components, [componentName.value]: {
    ...component.value, inputs: { ...component.value.inputs, [port.value]: description() } } } }
  return activationCycle(proposed) ? 'Activation cycle: simulated execution with --microsteps is required.' : ''
})
async function preview() {
  if (problem.value || store.busy || store.pending) return
  await store.command({ action: 'configure_input', component: componentName.value, port: port.value, description: description(), ...(props.compact && creating.value ? { create: true } : {}) })
}
function choose(event: Event) {
  const name = (event.target as HTMLSelectElement).value
  store.select(store.project.model, ['components', componentName.value, 'inputs', name])
}
</script>
<template>
  <section v-if="model?.version === 2 && component" class="execution-view" aria-label="Input execution and activation links">
    <h3>Input execution</h3>
    <p>Push calls its input callback. Pull is read during another activation. Timers carry no message.</p>
    <div v-if="!compact" class="input-choice"><label>Input<select :value="creating ? '' : port" aria-label="Execution input" @change="choose"><option value="" disabled>Choose an input</option><option v-for="name in Object.keys(component.inputs || {})" :key="name">{{ name }}</option></select></label><Button label="Add input" size="small" severity="secondary" :disabled="!!store.pending || store.busy" @click="newInput" /></div>
    <form v-if="port || creating" aria-label="Configure input" @submit.prevent="preview">
      <fieldset :disabled="!!store.pending || store.busy || store.project.documents[store.project.model].stale">
        <label v-if="creating">Input name<input v-model.trim="port" aria-label="New input name" required></label>
        <label>Kind<select v-model="draft.kind" aria-label="Input kind"><option value="message">Message</option><option value="timer">Timer</option></select></label>
        <template v-if="draft.kind === 'message'">
          <label>Mode<select v-model="draft.mode" aria-label="Input mode"><option value="push">Push · callback</option><option value="pull">Pull · passive</option></select></label>
          <label>Logical root<input v-model="draft.root" :list="typesId" aria-label="Input logical root" required><datalist :id="typesId"><option v-for="type in roots" :key="type" :value="type" /></datalist></label>
          <label>Schema override<input v-model="draft.schema" aria-label="Input schema override" placeholder="Use the model schema"></label>
          <template v-if="draft.mode === 'push'"><label class="check"><input v-model="draft.timeoutEnabled" type="checkbox" aria-label="Enable inactivity timeout"> Inactivity timeout</label><label v-if="draft.timeoutEnabled">Timeout (seconds)<input v-model="draft.timeout" aria-label="Input inactivity timeout" inputmode="decimal" required></label></template>
          <p v-else>Passive Pull has no callback or activating timeout. Switching to Pull removes timeout and activation links in the preview.</p>
        </template>
        <template v-else>
          <label>Timer mode<select v-model="draft.timerMode" aria-label="Timer mode"><option value="periodic">Periodic</option><option value="one_shot">One shot</option></select></label>
          <label>Delay (seconds)<input v-model="draft.delay" aria-label="Timer delay" inputmode="decimal" required></label>
          <label>Initial state<select v-model="draft.start" aria-label="Timer initial state"><option value="automatic">Armed automatically</option><option value="manual">Arm explicitly</option></select></label>
          <p>First expiration follows the delay. Timer inputs cannot receive subscriptions.</p>
        </template>
        <div v-if="events.length" class="activation-controls">
          <h4>Allowed output activations</h4><p>No selection means this event cannot publish an output.</p>
          <fieldset v-for="event in events" :key="event"><legend>{{ event === 'message' ? 'Message received' : 'Timeout' }} → output</legend>
            <label v-for="output in outputs" :key="output" class="check"><input v-if="event === 'message'" v-model="draft.messageOutputs" type="checkbox" :value="output" :aria-label="'Allow message to ' + output"><input v-else v-model="draft.timeoutOutputs" type="checkbox" :value="output" :aria-label="'Allow timeout to ' + output"> {{ output }}{{ component.outputs?.[output] ? '' : ' (missing output)' }}</label>
            <small v-if="!outputs.length">This component has no outputs.</small>
          </fieldset>
        </div>
        <p>Review the component callbacks when changing an input's kind or mode.</p>
        <p v-if="cycleWarning" class="execution-note" role="status">{{ cycleWarning }}</p>
        <p v-if="problem" class="error" role="status">{{ problem }}</p>
        <Button type="submit" label="Preview input changes" size="small" :disabled="!!problem" />
      </fieldset>
    </form>
  </section>
</template>
<style scoped>
.execution-view { margin-top: 20px; padding-top: 16px; border-top: 1px solid #e3eaf4; display: grid; gap: 10px; }
.execution-view p, .execution-view small { font-size: 11px; line-height: 1.5; }
.execution-view form > fieldset { display: grid; gap: 10px; border: 0; padding: 0; margin: 0; min-width: 0; }
.execution-view label { display: grid; gap: 4px; font-size: 11px; }
.execution-view select, .execution-view input:not([type=checkbox]) { width: 100%; padding: 6px; border: 1px solid #cbd5e1; border-radius: 4px; background: white; color: #1c3049; }
.execution-view .check { display: flex; flex-direction: row; align-items: center; gap: 6px; }
.input-choice { display: flex; align-items: end; gap: 8px; }.input-choice label { flex: 1; min-width: 0; }
.activation-controls { display: grid; gap: 8px; }.activation-controls h4 { font-size: 12px; margin: 0; }
.activation-controls fieldset { border: 1px solid #e0e7f2; border-radius: 5px; padding: 9px; min-width: 0; display: grid; gap: 6px; }
.activation-controls legend { font-size: 11px; color: #526b91; }
</style>

