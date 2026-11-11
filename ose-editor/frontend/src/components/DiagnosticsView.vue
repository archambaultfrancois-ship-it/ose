<script setup lang="ts">
import { computed } from 'vue'
import Button from 'primevue/button'
import { useProject } from '../store'
import { guideDiagnostic, type Diagnostic, type DiagnosticFix } from '../diagnostics'
import type { NavigationTarget } from '../navigation'
const props = defineProps<{ diagnostics: Diagnostic[] }>()
const emit = defineEmits<{ navigate: [target: NavigationTarget, source: boolean] }>()
const store = useProject()
const entries = computed(() => props.diagnostics.map(diagnostic => ({ diagnostic, guide: guideDiagnostic(store.project, diagnostic) })))
const unapplied = computed(() => Object.keys(store.sourceDrafts).length > 0)
function preview(fix: DiagnosticFix) { if (!store.busy && !store.pending && !unapplied.value) store.command(fix.command) }
</script>
<template>
  <article v-for="({ diagnostic, guide }, index) in entries" :key="index" class="guided-diagnostic" :data-severity="diagnostic.severity || 'error'">
    <header><strong>{{ diagnostic.severity || 'error' }} · {{ diagnostic.origin }}</strong><code v-if="diagnostic.file">{{ diagnostic.file }}</code></header>
    <p class="diagnostic-message">{{ diagnostic.message }}</p><p>{{ guide.explanation }}</p>
    <div v-for="(target, index) in guide.targets" :key="index" class="diagnostic-target">
      <span>{{ target.label }}</span><Button v-if="!target.line && target.path?.length" label="Open properties" size="small" severity="secondary" @click="emit('navigate', target, false)" /><Button label="Open source" size="small" variant="text" @click="emit('navigate', target, true)" />
    </div>
    <div v-for="fix in guide.fixes" :key="fix.label" class="diagnostic-fix"><p>{{ fix.detail }}</p><Button :label="'Preview: ' + fix.label" size="small" :disabled="store.busy || !!store.pending || unapplied" @click="preview(fix)" /></div>
    <small v-if="guide.fixes.length && unapplied">Preview and apply unfinished source edits before preparing a correction.</small>
  </article>
</template>
