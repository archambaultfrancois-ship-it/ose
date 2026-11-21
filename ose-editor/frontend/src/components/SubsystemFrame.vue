<script setup lang="ts">
import { onBeforeUnmount } from 'vue'
import { useVueFlow } from '@vue-flow/core'
import { resizeLayout, type FrameBounds as Bounds, type FrameLayout } from '../layout'
const props = defineProps<{ id: string; data: any }>()
const emit = defineEmits<{ resize: [layout: FrameLayout] }>()
const { findNode, getNodes, getViewport } = useVueFlow()
let resizing: { pointer: number; corner: string; x: number; y: number; zoom: number; initial: Bounds; nodes: any[]; layout: FrameLayout | null } | null = null
function apply(layout: FrameLayout) {
  for (const node of getNodes.value) {
    const frame = node.data.kind === 'definition'
    const key = frame || node.data.kind === 'component' || node.data.subsystem ? node.data.name : node.id
    const position = layout.positions[key], bounds = layout.frameBounds[key]
    if (position) node.position = { x: position.x - (frame ? 24 : 0), y: position.y - (frame ? 100 : 0) }
    if (bounds) node.style = { ...node.style, width: bounds.width + 'px', height: bounds.height + 'px' }
  }
}
function move(event: PointerEvent) {
  if (!resizing || event.pointerId !== resizing.pointer) return
  const { initial, corner, zoom } = resizing
  const dx = (event.clientX - resizing.x) / zoom, dy = (event.clientY - resizing.y) / zoom
  const left = corner.includes('w') ? Math.min(initial.x + dx, initial.x + initial.width - 344) : initial.x
  const top = corner.includes('n') ? Math.min(initial.y + dy, initial.y + initial.height - 244) : initial.y
  const right = corner.includes('e') ? Math.max(initial.x + initial.width + dx, initial.x + 344) : initial.x + initial.width
  const bottom = corner.includes('s') ? Math.max(initial.y + initial.height + dy, initial.y + 244) : initial.y + initial.height
  resizing.layout = resizeLayout(resizing.nodes, props.data.name, { x: left, y: top, width: right - left, height: bottom - top }, corner)
  apply(resizing.layout)
}
function clear() {
  resizing = null
  window.removeEventListener('pointermove', move)
  window.removeEventListener('pointerup', finish)
  window.removeEventListener('pointercancel', cancel)
}
function finish(event: PointerEvent) {
  if (!resizing || event.pointerId !== resizing.pointer) return
  move(event)
  const layout = resizing.layout!
  clear()
  emit('resize', layout)
}
function cancel() {
  if (resizing) for (const original of resizing.nodes) {
    const node = findNode(original.id)
    if (node) { node.position = { ...original.position }; node.style = { ...original.style } }
  }
  clear()
}
function start(event: PointerEvent, corner: string) {
  if (event.button !== 0) return
  const initial = { ...props.data.bounds }
  resizing = { pointer: event.pointerId, corner, x: event.clientX, y: event.clientY, zoom: getViewport().zoom, initial, nodes: getNodes.value.map(node => ({ ...node, position: { ...node.position }, style: { ...node.style } })), layout: null }
  const handle = event.currentTarget as HTMLElement
  handle.setPointerCapture(event.pointerId)
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', finish)
  window.addEventListener('pointercancel', cancel)
}
onBeforeUnmount(clear)
</script>
<template>
  <section class="subsystem-frame" :aria-label="'Expanded subsystem ' + data.name">
    <header><strong>{{ data.name }}</strong><code>{{ data.file }}</code></header>
    <div class="public-interface">
      <span v-for="direction in ['inputs', 'outputs', 'parameters']" :key="direction"><strong>{{ direction }}</strong> {{ Object.keys(data.interface[direction] || {}).join(', ') || '—' }}</span>
    </div>
    <button v-for="corner in ['nw', 'ne', 'sw', 'se']" :key="corner" type="button"
      class="resize-corner nodrag nopan" :class="corner" :aria-label="'Resize ' + data.name + ' ' + corner"
      @pointerdown.stop.prevent="start($event, corner)" @dblclick.stop />
  </section>
</template>
<style scoped>
.subsystem-frame { width: 100%; height: 100%; box-sizing: border-box; border: 2px dashed #7e98bf; border-radius: 14px; position: relative; background: #bdd2f059; cursor: grab; }
.resize-corner { position: absolute; width: 12px; height: 12px; padding: 0; border: 1px solid #4c6d9d; border-radius: 3px; background: #fff; touch-action: none; }
.nw { top: -7px; left: -7px; cursor: nwse-resize; }
.ne { top: -7px; right: -7px; cursor: nesw-resize; }
.sw { bottom: -7px; left: -7px; cursor: nesw-resize; }
.se { bottom: -7px; right: -7px; cursor: nwse-resize; }
header { display: flex; align-items: baseline; flex-wrap: wrap; gap: 16px; padding: 14px 18px 8px; color: #28456c; }
header strong { font-size: 16px; } code { font-size: 11px; }
.public-interface { display: flex; flex-wrap: wrap; gap: 8px 20px; padding: 0 18px; font-size: 11px; color: #536d91; }
</style>
