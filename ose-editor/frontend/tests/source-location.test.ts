// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PrimeVue from 'primevue/config'
import { EditorView } from '@codemirror/view'
import { foldEffect, foldedRanges } from '@codemirror/language'
import { jsonItemStart } from '../src/source-location'
import SourceEditor from '../src/components/SourceEditor.vue'
import App from '../src/App.vue'
import { useProject } from '../src/store'

const source = '{\n  "description": "🔎 é",\n  "components": {\n    "node":\n    {\n      "inputs": { "value": { "root": "Value" } }\n    }\n  },\n  "subscriptions": [\n    { "id": "first" },\n    { "id": "second" }\n  ]\n}'

describe('JSON source locations', () => {
  it('locates the property opening line, including Unicode before it and separated braces', () => {
    expect(jsonItemStart(source, ['components', 'node'])).toBe(source.indexOf('"node"'))
    expect(jsonItemStart(source, ['components', 'node', 'inputs', 'value'])).toBe(source.indexOf('"value"'))
    expect(jsonItemStart(source, ['subscriptions', '1'])).toBe(source.indexOf('{ "id": "second" }'))
  })
  it('resolves escaped property names and array scalars without confusing repeated names', () => {
    const text = '{ "unrelated": { "same": 1 }, "a\\"b": { "same": [true, false, null, 18446744073709551615] } }'
    expect(jsonItemStart(text, ['a"b', 'same', '3'])).toBe(text.indexOf('18446744073709551615'))
    expect(jsonItemStart(text, ['a"b', 'same'])).toBe(text.lastIndexOf('"same"'))
    expect(jsonItemStart(text, ['a"b', 'same', '2'])).toBe(text.indexOf('null'))
  })
  it('does not invent a position when the inspected path no longer exists in a draft', () => {
    expect(jsonItemStart(source, ['components', 'deleted'])).toBeNull()
    expect(jsonItemStart(source, ['subscriptions', '99'])).toBeNull()
    expect(jsonItemStart(source, ['description', 'child'])).toBeNull()
  })
})

let wrapper: VueWrapper | undefined
// Happy DOM fires native selectionchange synchronously, unlike browsers.
// Keep real CodeMirror transactions, but test the focus request separately.
beforeEach(() => { vi.spyOn(EditorView.prototype, 'focus').mockImplementation(() => {}) })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks(); document.body.innerHTML = '' })

async function editor(text = source) {
  const pinia = createPinia(); setActivePinia(pinia)
  const store = useProject(); store.project = { revision: 'one' }
  const api = vi.spyOn(store, 'api').mockResolvedValue({ text: source })
  if (text !== source) store.sourceDrafts['model.json'] = text
  wrapper = mount(SourceEditor, { attachTo: document.body,
    props: { file: 'model.json', format: 'json', target: { file: 'model.json', path: ['components', 'node'], request: 1 } },
    global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
  await flushPromises()
  const view = EditorView.findFromDOM(wrapper.get('.cm-editor').element as HTMLElement)!
  return { view, store, api }
}

describe('source editor navigation', () => {
  it('focuses the item line on load and navigates again without reloading or changing the draft', async () => {
    const { view, api, store } = await editor()
    expect(view.state.doc.lineAt(view.state.selection.main.head).number).toBe(4)
    expect(view.focus).toHaveBeenCalled()
    const draft = '\n\n' + view.state.doc.toString()
    view.dispatch({ changes: { from: 0, insert: '\n\n' } })
    await wrapper!.setProps({ target: { file: 'model.json', path: ['subscriptions', '1'], request: 2 } })
    expect(view.state.doc.lineAt(view.state.selection.main.head).text.trim()).toBe('{ "id": "second" }')
    expect(view.state.doc.toString()).toBe(draft)
    expect(store.sourceDrafts['model.json']).toBe(draft)
    expect(api).toHaveBeenCalledTimes(1)
    view.dispatch({ selection: { anchor: 0 } })
    await wrapper!.setProps({ target: { file: 'model.json', path: ['subscriptions', '1'], request: 3 } })
    expect(view.state.selection.main.head).toBe(view.state.doc.lineAt(draft.indexOf('{ "id": "second" }')).from)
  })
  it('resolves current unapplied text and unfolds ancestors that hide the target line', async () => {
    const { view } = await editor('\n' + source)
    expect(view.state.doc.lineAt(view.state.selection.main.head).number).toBe(5)
    const text = view.state.doc.toString(), from = text.indexOf('{', text.indexOf('"components"')) + 1
    const to = text.indexOf('\n  },')
    view.dispatch({ effects: foldEffect.of({ from, to }) })
    expect(foldedRanges(view.state).size).toBe(1)
    await wrapper!.setProps({ target: { file: 'model.json', path: ['components', 'node', 'inputs'], request: 2 } })
    expect(foldedRanges(view.state).size).toBe(0)
    expect(view.state.doc.lineAt(view.state.selection.main.head).text).toContain('"inputs"')
  })
  it('passes the inspected JSON path from Show source on every click', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const store = useProject()
    store.project = { name: 'Example', revision: 'one', model: 'model.json', deployments: [],
      documents: { 'model.json': { id: 'model.json', role: 'model', format: 'json', value: {}, tree: { children: [] } } },
      workspace: { groups: {}, positions: {}, pinned: [] } }
    store.selection = { file: 'model.json', path: ['subscriptions', '1'] }
    wrapper = mount(App, { global: { plugins: [pinia, [PrimeVue, { unstyled: true }]],
      stubs: { GraphView: true, SourceEditor: true, ReceptionView: true, ContractView: true } } })
    await wrapper.get('button[title="Show source"]').trigger('click')
    expect(wrapper.findComponent(SourceEditor).props('target')).toEqual({ file: 'model.json', path: ['subscriptions', '1'], request: 1 })
    store.select('model.json', ['components', 'node'])
    await wrapper.get('button[title="Show source"]').trigger('click')
    expect(wrapper.findComponent(SourceEditor).props('target')).toEqual({ file: 'model.json', path: ['components', 'node'], request: 2 })
  })
})
