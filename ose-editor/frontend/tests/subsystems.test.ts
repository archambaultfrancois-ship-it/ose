// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { proxyRefs } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { VueFlow } from '@vue-flow/core'
import { useProject } from '../src/store'
import { graphModel, subsystemGraph, definitionScopes } from '../src/subsystems'
import { topology } from '../src/graph'
import { tableRows } from '../src/table-rows'
import { layoutGraph } from '../src/layout'
import { assessConnection, inputs, publishers } from '../src/connections'
import SubsystemTools from '../src/components/SubsystemTools.vue'
import GraphView from '../src/components/GraphView.vue'
import { navigationIndex } from '../src/navigation'

function fixture(): any {
  return { model: 'model.json', revision: 'one', deployments: ['deployment.json'], selected_deployment: 'deployment.json',
    workspace: { positions: {}, groups: {}, pinned: [], models: { 'child/model.json': { positions: { processor: { x: 100, y: 50 } } } } },
    documents: {
      'model.json': { value: { version: 2, schemas: 'value.sdl', components: {
        sensor: { inputs: {}, outputs: { out: { root: 'Value' } } },
      }, subsystems: { detection: { source: 'child/subsystem.json' }, backup: { source: 'child/subsystem.json' } },
      subscriptions: [{ id: 'measurements', from: 'sensor.out', to: 'detection.in' }] } },
      'child/model.json': { value: { version: 2, schemas: '../value.sdl', components: {
        processor: { inputs: { input: { root: 'Value' } }, outputs: { out: { root: 'Value' } } },
      }, subscriptions: [] } },
      'child/subsystem.json': { value: { version: 1, interface: { inputs: { in: { to: 'processor.input' } } } } },
    },
    model_contexts: { 'model.json': { definitions: [], deployments: ['deployment.json'] }, 'child/model.json': { definitions: ['child/subsystem.json'], deployments: ['child/deployment.json'] } },
    schema_contexts: { 'child/model.json': { '../value.sdl': 'value.sdl' } },
    subsystems: { 'model.json': Object.fromEntries(['detection', 'backup'].map(name => [name, { definition: 'child/subsystem.json', model: 'child/model.json', deployment: 'child/deployment.json', inputs: { in: { root: 'Value', schema: 'value.sdl' } }, outputs: { out: { root: 'Value', schema: 'value.sdl' } } }])) },
  }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
describe('subsystem authoring', () => {
  it('shows only public ports and routes graph connections to the instance', () => {
    const project = fixture(), before = JSON.stringify(project)
    const model = graphModel(project), graph = topology(model, project.workspace)
    expect(graph.nodes.find(node => node.id === 'subsystem:detection')?.data.inputs.map((port: any) => port.endpoint)).toEqual(['detection.in'])
    expect(graph.edges[0]).toMatchObject({ source: 'component:sensor', target: 'subsystem:detection', targetHandle: 'detection.in' })
    expect(inputs(model)).toContain('backup.in')
    expect(publishers(model)).toContainEqual({ endpoint: 'detection.out', external: false })
    expect(assessConnection(project, { endpoint: 'sensor.out', external: false }, 'detection.in').status).toBe('unknown')
    expect(assessConnection(project, { endpoint: 'sensor.out', external: false }, 'detection.processor.input').status).toBe('incompatible')
    expect(JSON.stringify(project)).toBe(before)
  })
  it('expands existing subsystem groups and preserves boundary links and shared source ownership', () => {
    const project = fixture(), before = JSON.stringify(project)
    const collapsed = topology(subsystemGraph(project), project.workspace)
    expect(collapsed.nodes.find(node => node.id === 'subsystem:detection')?.data).toMatchObject({ kind: 'group', subsystem: true })
    const expanded = topology(subsystemGraph(project, ['detection']), project.workspace)
    expect(expanded.nodes.map(node => node.id)).toContain('component:detection.processor')
    const frame = expanded.nodes.find(node => node.id === 'definition:detection')!
    expect(frame.type).toBe('subsystem-frame')
    expect(frame.data.file).toBe('child/subsystem.json')
    expect(frame.data.interface.inputs.in.to).toBe('processor.input')
    expect(frame.data.members).toContain('detection.processor')
    expect(frame.draggable).toBe(true)
    expect(layoutGraph(expanded.nodes, expanded.edges).children.find(node => node.id === frame.id)?.children.map((node: any) => node.id)).toContain('component:detection.processor')
    const leaf = expanded.nodes.find(node => node.id === 'component:detection.processor')!
    expect(frame.position.x).toBeLessThan(leaf.position.x)
    expect(frame.position.y).toBeLessThan(leaf.position.y)
    expect(parseFloat(frame.style.width)).toBeGreaterThan(400)
    expect(expanded.nodes.map(node => node.id)).toContain('subsystem:backup')
    expect(expanded.edges[0]).toMatchObject({ source: 'component:sensor', target: 'component:detection.processor', targetHandle: 'detection.processor:input' })
    expect(expanded.nodes.find(node => node.id === 'component:detection.processor')?.data).toMatchObject({ scope: ['detection'], localName: 'processor', model: 'child/model.json' })
    expect(JSON.stringify(project)).toBe(before)
    expect(topology(subsystemGraph(project), project.workspace).nodes.map(node => node.id)).toContain('subsystem:detection')
  })
  it('expands multiple instances with distinct internal links', () => {
    const project = fixture(), child = project.documents['child/model.json'].value
    child.components.sink = { inputs: { input: { root: 'Value' } }, outputs: {} }
    child.subscriptions.push({ id: 'internal', from: 'processor.out', to: 'sink.input' })
    const graph = topology(subsystemGraph(project, ['detection', 'backup']), project.workspace)
    expect(graph.edges.find(edge => edge.id === 'detection.internal')).toMatchObject({ source: 'component:detection.processor', target: 'component:detection.sink' })
    expect(graph.edges.find(edge => edge.id === 'backup.internal')).toMatchObject({ source: 'component:backup.processor', target: 'component:backup.sink' })
  })
  it('resolves public aliases across nested expanded groups', () => {
    const project = fixture()
    project.documents['child/model.json'].value.subsystems = { nested: { source: '../leaf/subsystem.json' } }
    project.documents['child/subsystem.json'].value.interface.inputs.in.to = 'nested.in'
    project.documents['leaf/model.json'] = { value: { version: 2, schemas: '../value.sdl', components: {
      processor: { inputs: { input: { root: 'Value' } }, outputs: {} },
    }, subscriptions: [] } }
    project.documents['leaf/subsystem.json'] = { value: { interface: { inputs: { in: { to: 'processor.input' } } } } }
    project.subsystems['child/model.json'] = { nested: { definition: 'leaf/subsystem.json', model: 'leaf/model.json', inputs: { in: { root: 'Value' } }, outputs: {} } }
    expect(definitionScopes(project, 'detection')).toEqual(['detection', 'detection.nested'])
    const graph = topology(subsystemGraph(project, definitionScopes(project, 'detection')), project.workspace)
    expect(graph.edges.find(edge => edge.id === 'measurements')).toMatchObject({ target: 'component:detection.nested.processor', targetHandle: 'detection.nested.processor:input' })
    const processor = graph.nodes.find(node => node.id === 'component:detection.nested.processor')!
    expect(processor.data.scope).toEqual(['detection', 'nested'])
    expect(processor.data.inputs[0].publicAliases).toEqual(['detection:in', 'detection.nested:in'])
    const parent = graph.nodes.find(node => node.id === 'definition:detection')!
    const nested = graph.nodes.find(node => node.id === 'definition:detection.nested')!
    expect(parent.zIndex).toBeLessThan(nested.zIndex)
    expect(nested.zIndex).toBeLessThan(0)
  })
  it('includes isolated internal components when unfolding the full definition', () => {
    const project = fixture()
    project.documents['child/model.json'].value.components.isolated = { inputs: {}, outputs: {} }
    const graph = topology(subsystemGraph(project, definitionScopes(project, 'detection')), project.workspace)
    expect(graph.nodes.map(node => node.id)).toContain('component:detection.isolated')
    expect(graph.nodes.map(node => node.id)).toContain('component:detection.processor')
    expect(graph.edges).toHaveLength(1)
  })
  it('keeps component creation accessible with the explorer hidden', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
    const store = useProject(); store.project = fixture()
    const wrapper = mount(GraphView, { global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    await wrapper.findAll('button').find(button => button.text() === 'Add component')!.trigger('click')
    expect(wrapper.emitted('add-component')).toHaveLength(1)
    wrapper.unmount()
  })
  it('unfolds and folds a saved instance through the group controls, then edits its definition', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
    const store = useProject(); const project = fixture()
    project.documents['child/model.json'].value.components.sink = { inputs: { input: { root: 'Value' } }, outputs: {} }
    project.documents['child/model.json'].value.subscriptions = [{ id: 'internal', from: 'processor.out', to: 'sink.input' }]
    store.project = project
    const command = vi.spyOn(store, 'command').mockImplementation(async (command: any) => {
      if (command.action === 'workspace') {
        project.workspace = { ...project.workspace, ...command }
        project.revision += 'x'; store.project = { ...project }
      }
      return project
    })
    const wrapper = mount(GraphView, { global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    await flushPromises()
    wrapper.findComponent(VueFlow).vm.$emit('nodeDoubleClick', { node: { data: { subsystem: true, name: 'detection' } } })
    await flushPromises()
    expect(command).toHaveBeenCalledWith(expect.objectContaining({ views: { expanded_subsystems: ['detection'] } }), false)
    expect(wrapper.find('[data-id="component:detection.processor"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="Expanded subsystem detection"]').exists()).toBe(true)
    expect(wrapper.get('[aria-label="Expanded subsystem detection"]').text()).toContain('child/subsystem.json')
    expect(wrapper.find('[data-id="subsystem:detection"]').exists()).toBe(false)
    expect(store.project.model).toBe('model.json')
    const flow = proxyRefs(wrapper.getComponent(VueFlow).vm.$.exposed!)
    expect(flow.getEdges.map((edge: any) => edge.id).sort()).toEqual(['detection.internal', 'measurements'])
    await wrapper.get('[aria-label="Expanded subsystem detection"] header').trigger('dblclick')
    await flushPromises()
    expect(wrapper.find('[data-id="subsystem:detection"]').exists()).toBe(true)
    expect(flow.getEdges.map((edge: any) => edge.id)).toEqual(['measurements'])
    expect(wrapper.find('nav[aria-label="Expanded subsystems"]').exists()).toBe(false)
    await wrapper.get('[data-id="subsystem:detection"] button').trigger('click'); await flushPromises()
    expect(store.project.model).toBe('child/model.json')
    expect(store.modelTrail.at(-1)?.name).toBe('detection')
    wrapper.unmount()
  })
  it('resizes expanded frames from four corners without cropping their contents', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
    const store = useProject(), project = fixture()
    project.workspace.views = { expanded_subsystems: ['detection'] }
    store.project = project
    const command = vi.spyOn(store, 'command').mockImplementation(async (command: any) => {
      project.workspace = { ...project.workspace, ...command }
      project.revision += 'x'; store.project = { ...project }
      return project
    })
    const wrapper = mount(GraphView, { global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    await flushPromises()
    const flow = proxyRefs(wrapper.getComponent(VueFlow).vm.$.exposed!)
    const frame = () => flow.getNodes.find((node: any) => node.id === 'definition:detection')!
    const initial = { ...frame().data.bounds }
    const leaf = { ...flow.getNodes.find((node: any) => node.id === 'component:detection.processor').position }
    expect(wrapper.findAll('.resize-corner').map(handle => handle.attributes('aria-label'))).toEqual([
      'Resize detection nw', 'Resize detection ne', 'Resize detection sw', 'Resize detection se',
    ])
    async function resize(corner: string, dx: number, dy: number) {
      const handle = wrapper.get('.resize-corner.' + corner)
      Object.defineProperty(handle.element, 'setPointerCapture', { value: vi.fn(), configurable: true })
      await handle.trigger('pointerdown', { pointerId: 1, button: 0, clientX: 100, clientY: 100 })
      window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, clientX: 100 + dx, clientY: 100 + dy }))
      expect(command).not.toHaveBeenCalled()
      window.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1, clientX: 100 + dx, clientY: 100 + dy }))
      await flushPromises()
    }
    await resize('se', 60, 40)
    expect(project.workspace.views.frame_bounds.detection).toEqual({ ...initial, width: initial.width + 60, height: initial.height + 40 })
    expect(frame().data.bounds).toEqual(project.workspace.views.frame_bounds.detection)
    command.mockClear()
    await resize('nw', -30, -20)
    expect(frame().data.bounds).toEqual({ x: initial.x - 30, y: initial.y - 20, width: initial.width + 90, height: initial.height + 60 })
    const enlarged = { ...frame().data.bounds }
    command.mockClear()
    await resize('nw', 1000, 1000)
    expect(frame().data.bounds.x + frame().data.bounds.width).toBe(enlarged.x + enlarged.width)
    expect(frame().data.bounds.y + frame().data.bounds.height).toBe(enlarged.y + enlarged.height)
    command.mockClear()
    await resize('se', -1000, -1000)
    expect(frame().data.bounds.width).toBe(448)
    expect(frame().data.bounds.height).toBe(268)
    const moved = flow.getNodes.find((node: any) => node.id === 'component:detection.processor').position
    expect(moved).not.toEqual(leaf)
    expect(project.workspace.positions['detection.processor']).toEqual(moved)
    expect(flow.getEdges.map((edge: any) => edge.id)).toEqual(['measurements'])
    wrapper.unmount()
  })
  it('drags an expanded frame with its contents and saves the move once', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
    const store = useProject(), project = fixture()
    project.workspace.views = { expanded_subsystems: ['detection'] }
    store.project = project
    const command = vi.spyOn(store, 'command').mockResolvedValue(project)
    const wrapper = mount(GraphView, { global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    await flushPromises()
    const canvas = wrapper.getComponent(VueFlow), flow = proxyRefs(canvas.vm.$.exposed!)
    const frame = flow.getNodes.find((node: any) => node.id === 'definition:detection')!
    const leaf = flow.getNodes.find((node: any) => node.id === 'component:detection.processor')!
    const sensor = flow.getNodes.find((node: any) => node.id === 'component:sensor')!
    const before = { frame: { ...frame.position }, leaf: { ...leaf.position }, sensor: { ...sensor.position } }
    canvas.vm.$emit('nodeDragStart', { node: frame })
    frame.position = { x: before.frame.x + 120, y: before.frame.y + 75 }
    canvas.vm.$emit('nodeDrag', { node: frame })
    await flushPromises()
    expect(leaf.position).toEqual({ x: before.leaf.x + 120, y: before.leaf.y + 75 })
    expect(sensor.position).toEqual(before.sensor)
    expect(command).not.toHaveBeenCalled()
    canvas.vm.$emit('nodeDragStop', { node: frame })
    await flushPromises()
    expect(command).toHaveBeenCalledTimes(1)
    expect(command).toHaveBeenCalledWith({ action: 'workspace', positions: {
      detection: { x: before.frame.x + 120 + 24, y: before.frame.y + 75 + 100 },
      'detection.processor': { x: before.leaf.x + 120, y: before.leaf.y + 75 },
    } }, false)
    expect(flow.getEdges.map((edge: any) => edge.id)).toEqual(['measurements'])
    wrapper.unmount()
  })
  it('lists the visible graph in Tables with source paths for nested edits', () => {
    const project = fixture()
    expect(tableRows(project).map(row => row.name).sort()).toEqual(['backup', 'detection', 'measurements', 'sensor'])
    expect(tableRows(project, 'subscriptions')).toContainEqual(expect.objectContaining({ name: 'measurements', detail: 'sensor.out → detection.in', type: 'fifo (default)' }))
    project.workspace.views = { expanded_subsystems: ['detection'] }
    project.documents['child/model.json'].value.components.sink = { inputs: { input: { root: 'Value' } }, outputs: {} }
    project.documents['child/model.json'].value.subscriptions = [{ id: 'internal', from: 'processor.out', to: 'sink.input', qos: { buffer: 'latest' } }]
    const graph = topology(subsystemGraph(project, ['detection']), project.workspace)
    const rows = tableRows(project)
    expect(rows.map(row => row.name).sort()).toEqual([...graph.nodes.map(node => node.data.name), ...graph.edges.map(edge => edge.id)].sort())
    expect(tableRows(project, 'components')).toContainEqual(expect.objectContaining({ name: 'detection.processor', file: 'child/model.json', path: ['components', 'processor'] }))
    expect(tableRows(project, 'subscriptions')).toContainEqual(expect.objectContaining({ name: 'detection.internal', detail: 'detection.processor:out → detection.sink:input', type: 'latest', file: 'child/model.json', path: ['subscriptions', '0'] }))
    expect(tableRows(project, 'subscriptions')).toContainEqual(expect.objectContaining({ name: 'measurements', detail: 'sensor.out → detection.processor:input' }))
    expect(tableRows(project, 'ports')).toContainEqual(expect.objectContaining({ name: 'detection.processor:input', file: 'child/model.json', path: ['components', 'processor', 'inputs', 'input'] }))
    project.workspace.views.expanded_subsystems = []
    expect(tableRows(project, 'components').map(row => row.name)).toEqual(['sensor'])
    project.documents['model.json'].value.external_topics = { telemetry: { root: 'Value' } }
    project.documents['model.json'].value.subscriptions.push({ id: 'external', from_topic: 'telemetry', to: 'detection.in', qos: { buffer: 'latest' } })
    expect(tableRows(project, 'subscriptions')).toContainEqual(expect.objectContaining({ name: 'external', detail: 'telemetry → detection.in', type: 'latest' }))
  })
  it('matches folded visual groups and hides their internal connections in Tables', () => {
    const project = fixture()
    project.workspace.groups = { pair: { members: ['sensor', 'detection'], collapsed: true } }
    expect(tableRows(project).map(row => row.name).sort()).toEqual(['backup', 'pair'])
    expect(tableRows(project, 'components')).toEqual([])
    expect(tableRows(project, 'subscriptions')).toEqual([])
    expect(tableRows(project, 'ports').some(row => row.name === 'sensor.out')).toBe(false)
  })
  it('navigates shared definitions and returns to the parent with separate layouts', () => {
    setActivePinia(createPinia())
    const store = useProject(); store.project = fixture()
    store.openSubsystem('detection')
    expect(store.project.model).toBe('child/model.json')
    expect(store.project.workspace.positions.processor).toEqual({ x: 100, y: 50 })
    expect(store.project.root_selected_deployment).toBe('deployment.json')
    store.parentModel(0)
    expect(store.project.model).toBe('model.json')
    expect(store.project.workspace.positions).toEqual({})
    store.openSubsystem('backup')
    expect(store.project.model).toBe('child/model.json')
    expect(store.modelTrail.at(-1)?.name).toBe('backup')
  })
  it('sends nested edits to the definition model, preserving root selection', async () => {
    setActivePinia(createPinia())
    const store = useProject(); store.project = fixture(); store.openSubsystem('detection')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 'preview', revision: 'one' }) }))
    await store.command({ action: 'rename', kind: 'component', old: 'processor', name: 'filter' })
    const body = JSON.parse((vi.mocked(fetch).mock.calls[0][1] as any).body)
    expect(body.command.model).toBe('child/model.json')
    expect(store.pending.changes).toBeUndefined()
    expect(store.project.root_model).toBe('model.json')
  })
  it('indexes nested components and subsystem instances for navigation', () => {
    const index = navigationIndex(fixture())
    expect(index).toContainEqual(expect.objectContaining({ file: 'child/model.json', label: 'processor', kind: 'Component' }))
    expect(index).toContainEqual(expect.objectContaining({ label: 'detection', kind: 'Subsystem' }))
  })
  it('opens the shared definition from its graph card and returns with breadcrumbs', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
    const store = useProject(); store.project = fixture()
    const wrapper = mount(GraphView, { global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    await flushPromises()
    await wrapper.get('[data-id="subsystem:detection"] button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Editing shared definition: child/model.json')
    expect(wrapper.find('[data-id="component:processor"]').exists()).toBe(true)
    await wrapper.get('nav[aria-label="Subsystem hierarchy"] button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-id="subsystem:detection"]').exists()).toBe(true)
    wrapper.unmount()
  })
  it('previews an explicit instance and the selected-component extraction', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const store = useProject(); store.project = fixture()
    const command = vi.spyOn(store, 'command').mockResolvedValue({ id: 'preview' })
    const wrapper = mount(SubsystemTools, { props: { selected: ['sensor'] }, global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
    const button = (label: string) => wrapper.findAll('button').find(button => button.text() === label)!
    await button('Instantiate subsystem').trigger('click')
    await wrapper.get('[aria-label="Subsystem instance name"]').setValue('secondary')
    await wrapper.get('[aria-label="Subsystem definition path"]').setValue('child/subsystem.json')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(command).toHaveBeenLastCalledWith(expect.objectContaining({ action: 'instantiate_subsystem', name: 'secondary', source: 'child/subsystem.json' }))
    await button('Extract selection').trigger('click')
    await wrapper.get('[aria-label="Subsystem instance name"]').setValue('acquisition')
    await wrapper.get('[aria-label="Subsystem directory"]').setValue('acquisition')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(command).toHaveBeenLastCalledWith(expect.objectContaining({ action: 'extract_subsystem', components: ['sensor'], directory: 'acquisition' }))
    wrapper.unmount()
  })
})
