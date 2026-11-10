// @vitest-environment happy-dom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { topology, downstream } from '../src/graph'
import { keysFor, nodeLabel } from '../src/hints'
import { useProject } from '../src/store'
import JsonTree from '../src/components/JsonTree.vue'
import App from '../src/App.vue'
import ReceptionView from '../src/components/ReceptionView.vue'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
const model = {
  version: 2,
  components: {
    source: { inputs: {}, outputs: { out: { root: 'Reading' } } },
    compute: { inputs: { input: { root: 'Reading', activates: { message: ['result'] } } }, outputs: { result: { root: 'Result' } } },
    view: { inputs: { input: { root: 'Result' } }, outputs: {} },
  }, subscriptions: [{ id: 'readings', from: 'source.out', to: 'compute.input' }, { id: 'results', from: 'compute.result', to: 'view.input' }],
}

describe('domain to graph projection', () => {
  it('leaves unsupported models to source editing instead of applying v2 semantics', () => {
    expect(topology({ ...model, version: 1 }, {})).toEqual({ nodes: [], edges: [] })
    expect(keysFor('model', [], { version: 1 }).version).toBe('2')
    expect(keysFor('model', ['components', 'source'], { version: 1 })).not.toHaveProperty('trigger')
  })
  it('keeps multiple producers on one input handle and exposes shared reception', () => {
    const merged = { ...model, external_topics: { outside: { root: 'Reading' } }, subscriptions: [
      ...model.subscriptions, { id: 'outside', from_topic: 'outside', to: 'compute.input' },
    ] }
    const before = JSON.stringify(merged)
    const graph = topology(merged, {})
    const input = graph.nodes.find(node => node.id === 'component:compute')!.data.inputs[0]
    expect(input.endpoint).toBe('compute.input')
    expect(input.reception.subscriptions.map((sub: any) => sub.id)).toEqual(['readings', 'outside'])
    expect(input.reception.policy).toEqual({ buffer: 'fifo', capacity: 1024, overflow: 'reject_new', wait_timeout_ms: 100 })
    expect(graph.edges.filter(edge => edge.targetHandle === 'compute.input')).toHaveLength(2)
    expect(JSON.stringify(merged)).toBe(before)
  })
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
    const group = graph.nodes.find(node => node.id === 'group:acquisition')!
    expect(group.data.inputs).toEqual([])
    expect(group.data.outputs.map((port: any) => port.endpoint)).toEqual(['compute.result'])
  })
  it.each([false, true])('projects nested groups independently of their declaration order (parent first: %s)', parentFirst => {
    const parent = { pipeline: { members: [], groups: ['acquisition', 'processing'], collapsed: true } }
    const children = {
      acquisition: { members: ['source'], collapsed: true },
      processing: { members: ['compute'], collapsed: true },
    }
    const workspace = { groups: parentFirst ? { ...parent, ...children } : { ...children, ...parent } }
    const before = JSON.stringify(workspace)
    const closed = topology(model, workspace)
    expect(closed.nodes.map(node => node.id)).toEqual(['component:view', 'group:pipeline'])
    expect(closed.edges).toHaveLength(1)
    expect(closed.edges[0]).toMatchObject({ source: 'group:pipeline', sourceHandle: 'compute.result', target: 'component:view' })
    expect(closed.nodes[1].data.inputs).toEqual([])
    expect(closed.nodes[1].data.outputs.map((port: any) => port.endpoint)).toEqual(['compute.result'])
    expect(JSON.stringify(workspace)).toBe(before)
    workspace.groups.pipeline.collapsed = false
    const opened = topology(model, workspace)
    expect(opened.nodes.map(node => node.id)).toEqual(['component:view', 'group:acquisition', 'group:processing'])
    expect(opened.edges[0]).toMatchObject({ source: 'group:acquisition', target: 'group:processing' })
    workspace.groups.acquisition.collapsed = false
    expect(topology(model, workspace).nodes.map(node => node.id)).toEqual(['component:source', 'component:view', 'group:processing'])
  })
  it('supports a group containing both components and subgroups, including a third level', () => {
    const workspace = { groups: {
      acquisition: { members: ['source'], collapsed: true },
      processing: { members: ['compute'], groups: ['acquisition'], collapsed: true },
      pipeline: { members: ['view'], groups: ['processing'], collapsed: true },
    } }
    const closed = topology(model, workspace)
    expect(closed.nodes.map(node => node.id)).toEqual(['group:pipeline'])
    expect(closed.edges).toEqual([])
    workspace.groups.pipeline.collapsed = false
    expect(topology(model, workspace).nodes.map(node => node.id)).toEqual(['component:view', 'group:processing'])
  })
  it('ignores missing subgroup references and does not recurse indefinitely on cycles', () => {
    const graph = topology(model, { groups: {
      root: { members: ['source'], groups: ['missing', 'child'], collapsed: true },
      child: { members: ['compute'], groups: ['child'], collapsed: true },
    } })
    expect(graph.nodes.map(node => node.id)).toEqual(['component:view', 'group:root'])
    expect(graph.edges).toHaveLength(1)
    expect(graph.edges[0].source).toBe('group:root')
  })
  it('keeps group ports that also have crossing links and leaves free ports available', () => {
    const extended = { ...model, components: { ...model.components, spare: { inputs: { free: { root: 'Reading', mode: 'pull' } }, outputs: {} } },
      external_topics: { ambient: { root: 'Reading' } }, subscriptions: [...model.subscriptions,
        { id: 'fanout', from: 'source.out', to: 'view.input' },
        { id: 'external', from_topic: 'ambient', to: 'compute.input' },
      ] }
    const before = JSON.stringify(extended)
    const graph = topology(extended, { groups: { acquisition: { members: ['source', 'compute', 'spare'], collapsed: true } } })
    const group = graph.nodes.find(node => node.id === 'group:acquisition')!
    expect(group.data.inputs.map((port: any) => port.endpoint)).toEqual(['compute.input', 'spare.free'])
    expect(group.data.inputs[1].mode).toBe('pull')
    expect(group.data.outputs.map((port: any) => port.endpoint)).toEqual(['source.out', 'compute.result'])
    expect(graph.edges.map(edge => edge.id)).toEqual(['results', 'fanout', 'external'])
    for (const edge of graph.edges) {
      if (edge.source === group.id) expect(group.data.outputs.some((port: any) => port.endpoint === edge.sourceHandle)).toBe(true)
      if (edge.target === group.id) expect(group.data.inputs.some((port: any) => port.endpoint === edge.targetHandle)).toBe(true)
    }
    expect(JSON.stringify(extended)).toBe(before)
  })
  it('keeps connections between collapsed groups and restores internal links when expanded', () => {
    const workspace = { groups: {
      acquisition: { members: ['source', 'compute'], collapsed: true },
      display: { members: ['view'], collapsed: true },
    } }
    const collapsed = topology(model, workspace)
    expect(collapsed.edges).toHaveLength(1)
    expect(collapsed.edges[0]).toMatchObject({ source: 'group:acquisition', target: 'group:display', sourceHandle: 'compute.result', targetHandle: 'view.input' })
    expect(collapsed.nodes.find(node => node.id === 'group:display')!.data.inputs[0].endpoint).toBe('view.input')
    workspace.groups.acquisition.collapsed = false
    const expanded = topology(model, workspace)
    expect(expanded.edges).toHaveLength(2)
    expect(expanded.edges[0]).toMatchObject({ source: 'component:source', target: 'component:compute', sourceHandle: 'source.out', targetHandle: 'compute.input' })
    expect(expanded.nodes.find(node => node.id === 'component:source')!.data.outputs[0].endpoint).toBe('source.out')
  })
  it('treats input and output handles with the same name independently', () => {
    const bidirectional = { version: 2, components: {
      a: { inputs: { port: { root: 'Reading' } }, outputs: { port: { root: 'Reading' } } },
      b: { inputs: { port: { root: 'Reading' } }, outputs: {} },
    }, subscriptions: [{ id: 'internal', from: 'a.port', to: 'b.port' }] }
    const graph = topology(bidirectional, { groups: { pair: { members: ['a', 'b'], collapsed: true } } })
    expect(graph.nodes[0].data.inputs.map((port: any) => port.endpoint)).toEqual(['a.port'])
    expect(graph.nodes[0].data.outputs).toEqual([])
    expect(graph.edges).toEqual([])
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

describe('shared reception inspector', () => {
  it('previews one shared latest edit for all links without modifying source or delivery settings', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const store = useProject()
    const merged = { ...model, subscriptions: [...model.subscriptions,
      { id: 'outside', from_topic: 'outside', to: 'compute.input', qos: { ack: 'received', retry: {} } }] }
    const before = JSON.stringify(merged)
    store.project = { revision: 'one', model: 'model.json', documents: { 'model.json': { value: merged } } }
    store.selection = { file: 'model.json', path: ['components', 'compute', 'inputs', 'input'] }
    const command = vi.spyOn(store, 'command').mockResolvedValue(undefined)
    const wrapper = mount(ReceptionView, { global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    try {
      expect(wrapper.text()).toContain('2 incoming link(s)')
      expect(wrapper.text()).toContain('source.out')
      expect(wrapper.text()).toContain('outside')
      await wrapper.get('select[aria-label="Shared reception buffer"]').setValue('latest')
      expect((wrapper.get('input[aria-label="Shared reception capacity"]').element as HTMLInputElement).value).toBe('1')
      await wrapper.get('button').trigger('click')
      expect(store.selection?.path).toEqual(['subscriptions', '0'])
      await wrapper.findAll('button').find(button => button.text() === 'Apply shared reception')!.trigger('click')
      expect(command).toHaveBeenCalledWith({ action: 'set_reception', to: 'compute.input',
        reception: { buffer: 'latest', capacity: 1, overflow: 'drop_oldest', wait_timeout_ms: 100 } })
      expect(JSON.stringify(merged)).toBe(before)
    } finally { wrapper.unmount() }
  })
})

describe('shared revisioned command store', () => {
  it('applies visual grouping immediately and keeps it undoable', async () => {
    setActivePinia(createPinia())
    const store = useProject()
    const original = { revision: 'r1', workspace: { groups: {} } }
    const groups = { acquisition: { members: ['source', 'compute'], collapsed: true } }
    store.project = original
    const fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: 'group-change', revision: 'r1', changes: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ revision: 'r2', workspace: { groups }, can_undo: true }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ...original, revision: 'r3' }) })
    vi.stubGlobal('fetch', fetch)
    await store.command({ action: 'workspace', groups })
    expect(store.pending).toBeNull()
    expect(store.project.workspace.groups).toEqual(groups)
    expect(store.project.can_undo).toBe(true)
    expect(fetch.mock.calls[1][0]).toBe('/api/v1/commands/apply')
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ revision: 'r1', id: 'group-change' })
    await store.history()
    expect(fetch.mock.calls[2][0]).toBe('/api/v1/undo')
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ revision: 'r2' })
    expect(store.project.workspace.groups).toEqual({})
  })

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
  it('shows subscription IDs while keeping numeric paths for selection, renaming and editing', async () => {
    const tree = (value: any, path: string[] = []): any => ({
      name: path.at(-1), path, kind: Array.isArray(value) ? 'array' : value !== null && typeof value === 'object' ? 'object' : typeof value,
      literal: typeof value === 'object' && value !== null ? '' : JSON.stringify(value),
      children: value !== null && typeof value === 'object' ? Object.entries(value).map(([key, child]) => tree(child, [...path, key])) : [],
    })
    const pinia = createPinia(); setActivePinia(pinia)
    vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
    const store = useProject()
    const before = JSON.stringify(model)
    store.project = { name: 'Example', revision: 'revision', model: 'model.json', deployments: [], selected_deployment: null,
      documents: { 'model.json': { id: 'model.json', role: 'model', format: 'json', value: model, tree: tree(model) } },
      diagnostics: [], workspace: { positions: {}, groups: {}, pinned: [] }, validation: null }
    store.selection = { file: 'model.json', path: [] }
    const command = vi.spyOn(store, 'command').mockResolvedValue(undefined)
    const wrapper = mount(App, { attachTo: document.body, global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    try {
      const section = wrapper.findAll('.explorer-section').find(section => section.get('button').text().startsWith('subscriptions'))!
      const links = section.findAll('.explorer-object')
      expect(links.map(link => link.text())).toEqual(['readingsobject', 'resultsobject'])
      await links[0].trigger('click')
      await flushPromises()
      expect(store.selection?.path).toEqual(['subscriptions', '0'])
      expect(wrapper.get('.inspector h3').text()).toBe('readings')
      vi.stubGlobal('prompt', vi.fn().mockReturnValueOnce('readings_copy').mockReturnValueOnce('compute.second'))
      const duplicate = wrapper.findAll('button').find(button => button.text() === 'Duplicate')!
      await duplicate.trigger('click')
      expect(command).toHaveBeenLastCalledWith({ action: 'duplicate_subscription', copy: 'readings', name: 'readings_copy', to: 'compute.second' })
      const calls = command.mock.calls.length
      vi.stubGlobal('prompt', vi.fn().mockReturnValueOnce('cancelled').mockReturnValueOnce(null))
      await duplicate.trigger('click')
      expect(command).toHaveBeenCalledTimes(calls)
      vi.stubGlobal('prompt', vi.fn(() => 'renamed_readings'))
      await wrapper.findAll('button').find(button => button.text() === 'Rename references')!.trigger('click')
      expect(command).toHaveBeenLastCalledWith({ action: 'rename', kind: 'subscription', old: 'readings', name: 'renamed_readings' })
      await wrapper.get('.inspector input[aria-label="id"]').setValue('edited_readings')
      await wrapper.findAll('.inspector .scalar-editor').find(editor => editor.find('input[aria-label="id"]').exists())!.get('button').trigger('click')
      expect(command).toHaveBeenLastCalledWith({ action: 'set', file: 'model.json', path: ['subscriptions', '0', 'id'], literal: '"edited_readings"' })
      await section.get('button').trigger('click')
      await flushPromises()
      expect(wrapper.findAll('.inspector .property-branch > summary > span').map(label => label.text())).toContain('readings')
      expect(JSON.stringify(model)).toBe(before)
    } finally { wrapper.unmount() }
  })

  it('keeps numeric labels when subscription IDs are missing and for unrelated arrays', () => {
    const node = { name: '0', path: ['subscriptions', '0'], children: [] as any[] }
    expect(nodeLabel(node)).toBe('0')
    node.children = [{ name: 'id', kind: 'string', literal: '"readings"' }]
    expect(nodeLabel(node)).toBe('readings')
    expect(nodeLabel({ ...node, path: ['values', '0'] })).toBe('0')
    node.children[0].literal = 'unfinished'
    expect(nodeLabel(node)).toBe('0')
  })

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
