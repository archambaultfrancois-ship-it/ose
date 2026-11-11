<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import { useProject } from '../store'
import { navigationIndex, searchNavigation, type NavigationTarget } from '../navigation'
const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{ 'update:visible': [value: boolean]; navigate: [target: NavigationTarget, source: boolean] }>()
const store = useProject(), query = ref(''), active = ref(0)
const input = ref<{ $el: HTMLInputElement }>()
const index = computed(() => navigationIndex(store.project))
const matches = computed(() => searchNavigation(index.value, query.value))
const results = computed(() => matches.value.slice(0, 80))
watch(() => props.visible, visible => { if (visible) { query.value = ''; active.value = 0 } })
watch(results, () => { active.value = 0 })
async function move(delta: number) {
  if (!results.value.length) return
  active.value = (active.value + delta + results.value.length) % results.value.length
  await nextTick()
  document.getElementById('navigation-result-' + active.value)?.scrollIntoView?.({ block: 'nearest' })
}
function open(source = false, index = active.value) {
  const target = results.value[index]
  if (target) { emit('update:visible', false); emit('navigate', target, source) }
}
</script>
<template>
  <Dialog :visible="visible" modal header="Go to…" class="navigation-dialog" :style="{ width: 'min(700px, 94vw)' }" @update:visible="emit('update:visible', $event)" @show="input?.$el.focus()">
    <InputText ref="input" v-model="query" class="navigation-search" placeholder="Component, port, subscription, SDL declaration or file…" aria-label="Search project symbols" role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls="navigation-results" :aria-activedescendant="results.length ? 'navigation-result-' + active : undefined" @keydown.down.prevent="move(1)" @keydown.up.prevent="move(-1)" @keydown.enter.prevent="open($event.shiftKey)" />
    <p class="navigation-help">↑ ↓ to choose · Enter to open · Shift+Enter for source · Esc to close</p>
    <div id="navigation-results" role="listbox" aria-label="Project symbols" class="navigation-results">
      <div v-for="(item, index) in results" :id="'navigation-result-' + index" :key="item.id" role="option" :aria-selected="index === active" class="navigation-result" @click="open(false, index)" @mousemove="active = index">
        <span class="symbol-kind">{{ item.kind }}</span><strong>{{ item.label }}</strong><small v-if="item.stale">Stale projection</small><code>{{ item.context }}</code>
      </div>
    </div>
    <p v-if="!results.length" class="empty-state">No matching symbol or file.</p>
    <template #footer><small>{{ matches.length }} result(s){{ matches.length > 80 ? ' · showing the first 80; refine your search' : '' }}</small><Button label="Open source" severity="secondary" :disabled="!results.length" @click="open(true)" /><Button label="Open" :disabled="!results.length" @click="open()" /></template>
  </Dialog>
</template>
