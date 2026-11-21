<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
defineProps<{ label: string }>()
const menu = ref<HTMLDetailsElement>()
let blurTimer: ReturnType<typeof setTimeout> | undefined
function close(restoreFocus = false) {
  if (!menu.value?.open) return
  menu.value.open = false
  if (restoreFocus) menu.value.querySelector('summary')?.focus()
}
function outside(event: PointerEvent) {
  if (!menu.value?.contains(event.target as Node)) close()
}
function blur(event: FocusEvent) {
  clearTimeout(blurTimer)
  if (event.relatedTarget) {
    if (!menu.value?.contains(event.relatedTarget as Node)) close()
  } else {
    // Select options briefly release focus before returning it to the control.
    blurTimer = setTimeout(() => {
      if (!menu.value?.contains(document.activeElement)) close()
    }, 0)
  }
}
function windowBlur() { close() }
onMounted(() => {
  document.addEventListener('pointerdown', outside, true)
  window.addEventListener('blur', windowBlur)
})
onBeforeUnmount(() => {
  clearTimeout(blurTimer)
  document.removeEventListener('pointerdown', outside, true)
  window.removeEventListener('blur', windowBlur)
})
</script>
<template>
  <details ref="menu" name="graph-toolbar" class="toolbar-menu" @focusout="blur" @keydown.esc.stop.prevent="close(true)">
    <summary>{{ label }}</summary>
    <div class="toolbar-menu-content"><slot /></div>
  </details>
</template>
<style scoped>
.toolbar-menu { font-size: 11px; }
.toolbar-menu > summary { padding: 5px 8px; line-height: 16px; }
.toolbar-menu-content { width: 210px; padding: 8px; gap: 8px; }
.toolbar-menu-content :deep(label) { gap: 4px; font-size: 11px; line-height: 16px; }
.toolbar-menu-content :deep(.p-button), .toolbar-menu-content :deep(.p-select) { min-height: 28px; font-size: 11px !important; line-height: 16px; }
.toolbar-menu-content :deep(.p-button), .toolbar-menu-content :deep(.p-select-label) { padding: 5px 8px; }
.toolbar-menu-content :deep(.p-select-label) { font-size: 11px; line-height: 16px; }
.toolbar-menu-content :deep(.p-select-dropdown) { width: 26px; }
.toolbar-menu-content :deep(.p-select-dropdown .p-icon) { width: 11px; height: 11px; }
.toolbar-menu-content :deep(.p-select-list) { padding: 4px; gap: 2px; }
.toolbar-menu-content :deep(.p-select-option) { min-height: 28px; padding: 5px 8px; font-size: 11px; line-height: 16px; }
.toolbar-menu-content :deep(.inline) { min-height: 28px; padding: 5px 8px; }
.toolbar-menu-content :deep(input[type=checkbox]) { width: 12px; height: 12px; margin: 0; }
</style>
