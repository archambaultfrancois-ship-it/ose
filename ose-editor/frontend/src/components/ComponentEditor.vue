<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useId, watch } from 'vue'
import Button from 'primevue/button'
import { useProject } from '../store'
import { activationCycle, activationLinks, inputEvents, inputLabel, type ActivationLink } from '../execution'
import { selectActivation, selectedActivation } from '../component-editing'
const store = useProject(), marker = useId(), canvas = ref<SVGSVGElement>()
const model = computed(() => store.project?.documents[store.project.model]?.value)
const name = computed(() => store.editingComponent)
const component = computed(() => model.value?.components?.[name.value])
const inputs = computed(() => Object.entries(component.value?.inputs || {}) as [string, any][])
const outputs = computed(() => Object.entries(component.value?.outputs || {}) as [string, any][])
const links = computed(() => activationLinks(component.value))
const selected = computed(() => selectedActivation(store))
const height = computed(() => Math.max(inputs.value.length, outputs.value.length, 1) * 144 + 70)
const disabled = computed(() => !!store.pending || store.busy || store.project.documents[store.project.model].stale)
const cycle = computed(() => activationCycle(model.value))
const notice = ref(''), origin = ref<{ input: string; event: 'message' | 'timeout' } | null>(null)
const drag = ref<{ x: number; y: number; moved: boolean } | null>(null)
function row(index: number) { return 40 + index * 144 }
function eventY(input: string, event: string) { return row(inputs.value.findIndex(([port]) => port === input)) + (event === 'timeout' ? 78 : 45) }
function outputY(output: string) { return row(outputs.value.findIndex(([port]) => port === output)) + 55 }
function path(x: number, y: number, endX: number, endY: number) { return `M ${x} ${y} C ${x + 145} ${y}, ${endX - 145} ${endY}, ${endX} ${endY}` }
function isSelected(link: ActivationLink) { return selected.value && Object.entries(link).every(([key, value]) => (selected.value as any)[key] === value) }
function inspect(direction: string, port: string) { store.select(store.project.model, ['components', name.value, direction, port]) }
function portSelected(direction: string, port: string) { const p = store.selection?.path || []; return p[1] === name.value && p[2] === direction && p[3] === port }
function connections(direction: string, port: string) {
  return (model.value?.subscriptions || []).flatMap((sub: any, index: number) =>
    sub[direction === 'inputs' ? 'to' : 'from'] === name.value + '.' + port ? [{ ...sub, index }] : [])
}
function reference(direction: string, port: string) {
  const subs = connections(direction, port)
  if (!subs.length) return 'No external connection'
  return (direction === 'inputs' ? '← ' + (subs[0].from || subs[0].from_topic) : '→ ' + subs[0].to) + (subs.length > 1 ? ` (+${subs.length - 1})` : '')
}
function inspectConnection(direction: string, port: string) {
  const sub = connections(direction, port)[0]
  if (sub) store.select(store.project.model, ['subscriptions', String(sub.index)])
}
function short(text: string, maximum = 26) { return text?.length > maximum ? text.slice(0, maximum - 1) + '…' : text }
function chooseEvent(input: string, event: 'message' | 'timeout') {
  if (disabled.value) return
  inspect('inputs', input); origin.value = { input, event }; notice.value = `${input} · ${event}: choose an output.`
}
async function chooseOutput(output: string) {
  if (!origin.value) { inspect('outputs', output); return }
  if (disabled.value) return
  const link = { ...origin.value, output }
  origin.value = null
  if (links.value.some(candidate => Object.entries(link).every(([key, value]) => (candidate as any)[key] === value))) {
    notice.value = 'This activation link already exists.'; return
  }
  notice.value = ''
  await store.command({ action: 'configure_activation', component: name.value, ...link, enabled: true })
}
function point(event: PointerEvent) {
  const rect = canvas.value!.getBoundingClientRect()
  return { x: (event.clientX - rect.left) * 1000 / (rect.width || 1000), y: (event.clientY - rect.top) * height.value / (rect.height || height.value) }
}
function move(event: PointerEvent) { if (drag.value) drag.value = { ...point(event), moved: true } }
function stopDrag() {
  drag.value = null
  window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', finish); window.removeEventListener('pointercancel', cancel)
}
function cancel() { stopDrag(); origin.value = null; notice.value = '' }
function finish(event: PointerEvent) {
  // Touch devices may implicitly capture the pointer on its source anchor.
  const hit = document.elementFromPoint?.(event.clientX, event.clientY) || event.target
  const target = hit instanceof Element && canvas.value?.contains(hit) ? hit.closest('[data-output]')?.getAttribute('data-output') : null
  const moved = drag.value?.moved
  stopDrag()
  if (target) void chooseOutput(target)
  else if (moved) cancel()
}
function begin(event: PointerEvent, input: string, kind: 'message' | 'timeout') {
  if (disabled.value || event.button > 0) return
  stopDrag(); chooseEvent(input, kind)
  drag.value = { ...point(event), moved: false }
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', finish); window.addEventListener('pointercancel', cancel)
}
function removeLink() {
  if (!disabled.value && selected.value) store.command({ action: 'configure_activation', component: name.value, ...selected.value, enabled: false })
}
watch(() => [name.value, store.project?.revision, store.pending], cancel)
onBeforeUnmount(cancel)
</script>
<template>
  <section class="component-editor" :aria-label="'Edit component ' + name">
    <nav class="component-toolbar" aria-label="Component navigation"><Button label="← Application" aria-label="Back to application graph" severity="secondary" @click="store.closeComponent" /><span>/</span><button class="component-title" @click="store.select(store.project.model, ['components', name])">{{ name }}</button><span class="add-ports"><Button label="+ Input" size="small" :disabled="disabled" @click="store.createPort('message')" /><Button label="+ Timer" size="small" :disabled="disabled" @click="store.createPort('timer')" /><Button label="+ Output" size="small" :disabled="disabled" @click="store.createPort('output')" /></span></nav>
    <p class="instructions">Drag from message or timeout to an output, or select the event then the output. Select a link to edit it.</p>
    <p v-if="cycle" class="execution-warning" role="status">An activation cycle in this application requires simulated execution with --microsteps.</p>
    <p v-if="notice" role="status" class="connection-notice">{{ notice }}</p>
    <p v-if="store.project.documents[store.project.model].stale" class="error">Repair the model source to resume structured editing.</p>
    <div v-if="component" class="component-canvas">
      <svg ref="canvas" :viewBox="'0 0 1000 ' + height" width="1000" :height="height" role="group" aria-label="Component inputs, outputs and activation links" @keydown.esc.stop="cancel" @keydown.delete.prevent="removeLink" @keydown.backspace.prevent="removeLink">
        <defs><marker :id="marker" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M 0 0 L 7 3.5 L 0 7 z" fill="context-stroke" /></marker></defs>
        <text x="25" y="22" class="column-title">INPUTS / TIMERS</text><text x="735" y="22" class="column-title">OUTPUTS</text>
        <g v-for="link in links.filter(link => component.outputs?.[link.output])" :key="JSON.stringify(link)" class="activation-link" :class="[link.event, { selected: isSelected(link) }]" :data-link="JSON.stringify(link)" role="button" tabindex="0" :aria-label="link.input + ' ' + link.event + ' to ' + link.output" @click.stop="selectActivation(store, name, link)" @keydown.enter.prevent="selectActivation(store, name, link)" @keydown.space.prevent="selectActivation(store, name, link)">
          <path class="link-hit" :d="path(300, eventY(link.input, link.event), 700, outputY(link.output))" /><path class="link-line" :d="path(300, eventY(link.input, link.event), 700, outputY(link.output))" :marker-end="'url(#' + marker + ')'" /><title>{{ link.input }} · {{ link.event }} → {{ link.output }}</title>
        </g>
        <path v-if="drag && origin" class="drag-link" :d="path(300, eventY(origin.input, origin.event), drag.x, drag.y)" />
        <g v-for="([port, desc], index) in inputs" :key="'input:' + port" class="port-card" :class="{ selected: portSelected('inputs', port) }">
          <g role="button" tabindex="0" :aria-label="'Select input ' + port" @click="inspect('inputs', port)" @keydown.enter.prevent="inspect('inputs', port)" @keydown.space.prevent="inspect('inputs', port)"><rect x="20" :y="row(index)" width="270" height="115" rx="10" /><text x="35" :y="row(index) + 24" class="port-name">{{ short(port) }}</text><text x="35" :y="row(index) + 45" class="port-root">{{ short(desc.root || 'Timer · no message', 22) }}</text><text x="35" :y="row(index) + 68" class="port-mode">{{ short(inputLabel(desc), 24) }}</text><title>{{ port }} · {{ inputLabel(desc) }}</title></g>
          <g v-for="event in inputEvents(desc)" :key="event" class="event-anchor" :class="[event, { armed: origin?.input === port && origin.event === event, disabled }]" role="button" tabindex="0" :aria-label="'Connect ' + port + ' ' + event" :aria-disabled="disabled" @pointerdown.stop.prevent="begin($event, port, event)" @click.stop="chooseEvent(port, event)" @keydown.enter.prevent="chooseEvent(port, event)" @keydown.space.prevent="chooseEvent(port, event)"><text x="285" :y="eventY(port, event) - 10" text-anchor="end">{{ event }}</text><circle cx="300" :cy="eventY(port, event)" r="9" /></g>
          <g role="button" tabindex="0" :aria-label="'Connections for input ' + port" @click="inspectConnection('inputs', port)" @keydown.enter.prevent="inspectConnection('inputs', port)"><text x="35" :y="row(index) + 99" class="port-reference">{{ short(reference('inputs', port), 33) }}</text><title>{{ reference('inputs', port) }}</title></g>
        </g>
        <g v-for="([port, desc], index) in outputs" :key="'output:' + port" class="port-card" :class="{ selected: portSelected('outputs', port), 'output-target': origin }" :data-output="port">
          <g role="button" tabindex="0" :aria-label="'Select output ' + port" @click="chooseOutput(port)" @keydown.enter.prevent="chooseOutput(port)" @keydown.space.prevent="chooseOutput(port)"><rect x="720" :y="row(index)" width="260" height="115" rx="10" /><text x="735" :y="row(index) + 24" class="port-name">{{ short(port) }}</text><text x="735" :y="row(index) + 48" class="port-root">{{ short(desc.root) }}</text><circle cx="700" :cy="outputY(port)" r="9" /><title>{{ port }} · {{ desc.root }}</title></g>
          <g role="button" tabindex="0" :aria-label="'Connections for output ' + port" @click.stop="inspectConnection('outputs', port)" @keydown.enter.prevent.stop="inspectConnection('outputs', port)"><text x="735" :y="row(index) + 99" class="port-reference">{{ short(reference('outputs', port), 31) }}</text><title>{{ reference('outputs', port) }}</title></g>
        </g>
        <text v-if="!inputs.length" x="35" y="85" class="empty-label">Add an input or a timer.</text><text v-if="!outputs.length" x="735" y="85" class="empty-label">Add an output.</text>
      </svg>
    </div>
  </section>
</template>
<style scoped>
.component-editor { flex: 1; min-height: 0; display: flex; flex-direction: column; background: #f7f9fd; }
.component-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 12px 16px; border-bottom: 1px solid #dce4ef; background: white; }
.component-title { background: none; border: 0; color: #223c62; font-weight: 700; font-size: 15px; }.add-ports { display: flex; gap: 6px; margin-left: auto; }
.instructions, .connection-notice, .execution-warning { padding: 8px 16px; margin: 0; font-size: 12px; line-height: 1.5; }.execution-warning { background: #fff4dc; color: #805414; }.connection-notice { color: #3159a4; }
.component-canvas { flex: 1; min-height: 0; overflow: auto; padding: 12px; }.component-canvas svg { display: block; width: 100%; min-width: 720px; height: auto; touch-action: pan-y; }
.column-title { font: 600 12px sans-serif; fill: #6e819b; }.port-card rect { fill: white; stroke: #c9d6e7; stroke-width: 1.5; }.port-card.selected rect { stroke: #3159c7; stroke-width: 2.5; }.port-card.output-target rect { fill: #edf5ff; }
.port-card [role=button], .event-anchor, .activation-link { cursor: pointer; outline: none; }.port-card [role=button]:focus-visible rect, .port-card [role=button]:focus-visible circle, .event-anchor:focus-visible circle { stroke: #173f98; stroke-width: 4; }
.port-name { font: 600 15px sans-serif; fill: #223c62; }.port-root, .port-mode { font: 12px sans-serif; fill: #536d91; }.port-reference { font: 11px sans-serif; fill: #5776a4; text-decoration: underline; }.empty-label { font: 13px sans-serif; fill: #6e819b; }
.event-anchor text { font: 11px sans-serif; fill: #536d91; }.event-anchor circle, .port-card circle { fill: white; stroke: #4770bd; stroke-width: 2; }.event-anchor.timeout circle { stroke: #b48222; }.event-anchor.armed circle { fill: #4770bd; }.event-anchor.disabled { opacity: .5; cursor: default; }.event-anchor { touch-action: none; }
.activation-link path { fill: none; }.link-hit { stroke: transparent; stroke-width: 18; pointer-events: stroke; }.link-line { stroke: #4770bd; stroke-width: 2; pointer-events: none; }.timeout .link-line { stroke: #b48222; stroke-dasharray: 5 3; }.activation-link.selected .link-line, .activation-link:focus-visible .link-line { stroke-width: 4; }.drag-link { stroke: #3159c7; stroke-width: 2; stroke-dasharray: 5 3; fill: none; pointer-events: none; }
</style>
