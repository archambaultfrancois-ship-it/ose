<script setup lang="ts">
import { ref, watch, onBeforeUnmount, onMounted } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState, type StateEffect } from '@codemirror/state'
import { StreamLanguage, foldedRanges, unfoldEffect } from '@codemirror/language'
import { json } from '@codemirror/lang-json'
import Button from 'primevue/button'
import { useProject } from '../store'
import { jsonItemStart, type SourceTarget } from '../source-location'
const props = defineProps<{ file: string; format: string; target?: SourceTarget }>()
const store = useProject(), host = ref<HTMLElement>(), changed = ref(false)
let view: EditorView | undefined, baseline = '', generation = 0, loadedFile = ''
const sdl = StreamLanguage.define({
  startState: () => ({ comment: false }),
  token(stream, state) {
    if (state.comment) { if (stream.skipTo('*/')) { stream.match('*/'); state.comment = false } else stream.skipToEnd(); return 'comment' }
    if (stream.eatSpace()) return null
    if (stream.match('//')) { stream.skipToEnd(); return 'comment' }
    if (stream.match('/*')) { state.comment = true; return 'comment' }
    if (stream.match(/(?:message|structure|struct|enum|package|const|alias|required|optional|repeated|packed|wire)\b/)) return 'keyword'
    if (stream.match(/(?:u?int(?:8|16|32|64)|fl(?:32|64)|c(?:32|64)|bool|string)\b/)) return 'typeName'
    if (stream.match(/[+-]?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?/)) return 'number'
    stream.next(); return null
  },
})
async function load() {
  if (!host.value) return
  const current = ++generation
  const source = await store.api('document?file=' + encodeURIComponent(props.file))
  if (current !== generation) return
  baseline = source.text
  view?.destroy()
  const draft = store.sourceDrafts[props.file] ?? baseline
  changed.value = draft !== baseline
  view = new EditorView({ parent: host.value, state: EditorState.create({ doc: draft, extensions: [basicSetup, props.format === 'json' ? json() : sdl,
    EditorView.updateListener.of(update => { if (update.docChanged) { const text = update.state.doc.toString(); changed.value = text !== baseline; if (changed.value) store.sourceDrafts[props.file] = text; else delete store.sourceDrafts[props.file] } }),
    EditorView.theme({ '&': { height: '100%' }, '.cm-scroller': { overflow: 'auto', fontFamily: 'ui-monospace, monospace', fontSize: '13px' } }),
  ] }) })
  loadedFile = props.file
  reveal()
}
function reveal() {
  if (!view || props.format !== 'json' || loadedFile !== props.file || props.target?.file !== props.file) return
  const position = jsonItemStart(view.state.doc.toString(), props.target.path)
  if (position === null) return
  const line = view.state.doc.lineAt(position)
  const effects: StateEffect<unknown>[] = []
  foldedRanges(view.state).between(0, position, (from, to) => {
    if (from < position && to >= position) effects.push(unfoldEffect.of({ from, to }))
  })
  effects.push(EditorView.scrollIntoView(line.from, { y: 'start', yMargin: 24 }))
  view.dispatch({ selection: { anchor: line.from }, effects })
  view.focus()
}
async function commit() { if (view) await store.command({ action: 'source', file: props.file, text: view.state.doc.toString() }) }
watch(() => [props.file, store.project?.revision], load)
watch(() => props.target, reveal, { flush: 'post' })
onMounted(load)
onBeforeUnmount(() => { generation++; view?.destroy() })
</script>
<template><div class="source-editor"><div class="source-toolbar"><code>{{ file }}</code><Button label="Preview source changes" :disabled="!changed" @click="commit" /><small v-if="changed">Unapplied source draft</small></div><div ref="host" class="code-host" /></div></template>
