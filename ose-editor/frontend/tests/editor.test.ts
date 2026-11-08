// @vitest-environment happy-dom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { topology, downstream } from '../src/graph'
import { keysFor } from '../src/hints'
import { useProject } from '../src/store'
import JsonTree from '../src/components/JsonTree.vue'
import App from '../src/App.vue'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
const model = {
  components: {
    source: { inputs: {}, outputs: { out: { root: 'Reading' } }, trigger: { mode: 'periodic', period_ms: 100 } },
    compute: { inputs: { input: { root: 'Reading' } }, outputs: { result: { root: 'Result' } }, trigger: { mode: 'on_change' } },
    view: { inputs: { input: { root: 'Result', required: false } }, outputs: {}, trigger: { mode: 'on_change' } },
  }, subscriptions: [{ id: 'readings', from: 'source.out', to: 'compute.input' }, { id: 'results', from: 'compute.result', to: 'view.input' }],
}

describe('domain to graph projection', () => {
  it('retains endpoint handles and does not mutate the model', () => {
    const before = JSON.stringify(model)
    const graph = topology(model, {})
    expect(graph.nodes).toHaveLength(3)
    expect(graph.edges[0]).toMatchObject({ source: 'component:source', target: 'component:compute', sourceHandle: 'source.out', targetHandle: 'compute.input' })
    expect(JSON.stringify(model)).toBe(before)
  })
  it('preserves crossing ports when a visual group is collapsed', () => {
    const graph = topology(model, { groups: { acquisition: { members: ['source', 'compute'], collapsed: true } } })
    expect(graph.nodes).toHaveLength(2)
    expect(graph.edges).toHaveLength(1)
    expect(graph.edges[0]).toMatchObject({ source: 'group:acquisition', sourceHandle: 'compute.result', targetHandle: 'view.input' })
  })
  it('handles fan-out, external topics and dependency highlighting', () => {
    const external = { ...model, external_topics: { 'external.topic': { root: 'Reading' } }, subscriptions: [...model.subscriptions, { id: 'outside', from_topic: 'external.topic', to: 'compute.input' }] }
    const graph = topology(external, {})
    expect(graph.nodes.some(node => node.data.kind === 'external')).toBe(true)
    expect(downstream(model, 'compute', 'upstream')).toEqual(new Set(['compute', 'source']))
    expect(downstream(model, 'compute', 'downstream')).toEqual(new Set(['compute', 'view']))
  })
  it('keeps layout and link label preferences outside semantic data', () => {
    const graph = topology(model, { positions: { source: { x: 91, y: 27 } } }, false)
    expect(graph.nodes[0].position).toEqual({ x: 91, y: 27 })
    expect(graph.edges[0].label).toBe('')
  })
})

describe('shared revisioned command store', () => {
  it('prepares a concrete diff before changing the domain state', async () => {
    setActivePinia(createPinia())
    const store = useProject()
    store.token = 'access-token'; store.project = { revision: 'r1' }
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 'preview', revision: 'r1', changes: [{ file: 'model.json', diff: 'review' }] }) })
    vi.stubGlobal('fetch', fetch)
    await store.command({ action: 'rename', kind: 'component', old: 'source', name: 'new_source' })
    expect(store.project.revision).toBe('r1')
    expect(store.pending.changes[0].diff).toBe('review')
    expect(fetch.mock.calls[0][1].headers.Authorization).toBe('Bearer access-token')
  })
  it('retains the current project on a stale revision response', async () => {
    setActivePinia(createPinia())
    const store = useProject(); store.project = { revision: 'r1' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ message: 'Project changed' }) }))
    await store.command({ action: 'workspace' })
    expect(store.error).toBe('Project changed')
    expect(store.project.revision).toBe('r1')
  })
})

describe('structured property controls', () => {
  it('offers the complete advanced deployment and wire fields', () => {
    expect(Object.keys(keysFor('deployment', ['broker', 'external']))).toEqual(['enabled', 'max_clients', 'max_subscriptions', 'max_capacity', 'max_topics', 'max_catalogues', 'max_wait_timeout_ms'])
    expect(Object.keys(keysFor('schema', ['messages', 'Reading', 'fields', '0', 'wire']))).toEqual(['bits', 'q', 'encoding', 'align', 'pad_after'])
  })
  it('submits uint64 as an exact literal string', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const store = useProject(); const command = vi.spyOn(store, 'command').mockResolvedValue(undefined)
    const wrapper = mount(JsonTree, { props: { file: 'model.json', role: 'model', node: { name: 'maximum', path: ['maximum'], kind: 'number', literal: '18446744073709551615', children: [] } }, global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    await wrapper.find('input').setValue('18446744073709551614')
    await wrapper.find('button').trigger('click')
    expect(command).toHaveBeenCalledWith({ action: 'set', file: 'model.json', path: ['maximum'], literal: '18446744073709551614' })
    wrapper.unmount()
  })
  it('bounds large sequences to one visible page', () => {
    const pinia = createPinia()
    const children = Array.from({ length: 2048 }, (_, i) => ({ name: String(i), path: ['values', String(i)], kind: 'number', literal: String(i), children: [] }))
    const wrapper = mount(JsonTree, { props: { file: 'model.json', role: 'model', node: { path: ['values'], kind: 'array', children } }, global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    expect(wrapper.findAll('.property-branch')).toHaveLength(40)
    expect(wrapper.text()).toContain('1 / 52')
    wrapper.unmount()
  })
})

describe('assembled workspace', () => {
  it('renders the real Vue Flow nodes and authoring controls', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
    const store = useProject()
    store.project = { name: 'Example', revision: 'revision', model: 'model.json', deployments: [], selected_deployment: null,
      documents: { 'model.json': { id: 'model.json', role: 'model', format: 'json', value: model, tree: { path: [], kind: 'object', children: [] } } },
      diagnostics: [], workspace: { positions: {}, groups: {}, pinned: [] }, can_undo: false, can_redo: false, validation: null }
    store.selection = { file: 'model.json', path: [] }
    const wrapper = mount(App, { attachTo: document.body, global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    await new Promise(resolve => setTimeout(resolve, 150))
    expect(wrapper.text()).toContain('Model Editor')
    expect(wrapper.text()).toContain('Auto layout')
    expect(wrapper.findAll('.component-node')).toHaveLength(3)
    expect(wrapper.text()).toContain('source')
    expect(wrapper.text()).toContain('3 components')
    wrapper.unmount()
  })
})
