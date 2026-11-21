// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { useProject } from '../src/store'
import { graphModel } from '../src/subsystems'
import { topology } from '../src/graph'
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
