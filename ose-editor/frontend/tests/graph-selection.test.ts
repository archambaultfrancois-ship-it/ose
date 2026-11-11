// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick, proxyRefs } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import Select from 'primevue/select'
import { VueFlow } from '@vue-flow/core'
import GraphView from '../src/components/GraphView.vue'
import { useProject } from '../src/store'

let wrapper: VueWrapper | undefined
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function graph() {
  const pinia = createPinia()
  setActivePinia(pinia)
  vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
  const store = useProject()
  store.project = {
    revision: 'r1', model: 'model.json',
    documents: { 'model.json': { value: {
      version: 2,
      components: {
        source: { inputs: {}, outputs: {} },
        compute: { inputs: {}, outputs: {} },
        view: { inputs: {}, outputs: {} },
      }, external_topics: { ambient: { root: 'Reading' } }, subscriptions: [],
    } } }, workspace: { positions: {}, groups: {}, pinned: [] },
  }
  store.selection = { file: 'model.json', path: [] }
  const command = vi.spyOn(store, 'command').mockResolvedValue(undefined)
  wrapper = mount(GraphView, { attachTo: document.body,
    global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
  await flushPromises()
  return { store, command, wrapper }
}

async function click(name: string, modifier?: 'Shift' | 'Control' | 'Meta') {
  const flags = { shiftKey: modifier === 'Shift', ctrlKey: modifier === 'Control', metaKey: modifier === 'Meta' }
  if (modifier) {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: modifier, code: modifier + 'Left', ...flags }))
    await nextTick()
  }
  await wrapper!.get(`[data-id="${name}"]`).trigger('click', { button: 0, ...flags })
  if (modifier) document.dispatchEvent(new KeyboardEvent('keyup', { key: modifier, code: modifier + 'Left' }))
  await flushPromises()
}

function selected() {
  return wrapper!.findAll('.vue-flow__node.selected').map(node => node.attributes('data-id')).sort()
}

describe('graph component selection', () => {
  it.each(['Shift', 'Control', 'Meta'] as const)('selects a mouse rectangle with %s + drag without moving the viewport', async modifier => {
    const { wrapper, command } = await graph()
    const flow = proxyRefs(wrapper.getComponent(VueFlow).vm.$.exposed!)
    const viewport = { ...flow.viewport }
    // Happy DOM does not measure nodes; provide their rendered dimensions.
    for (const node of flow.getNodes) {
      node.dimensions = { width: 400, height: 144 }
    }
    const pane = wrapper.get('.vue-flow__pane')
    const release = vi.fn()
    Object.assign(pane.element, { setPointerCapture: vi.fn(), releasePointerCapture: release })
    const flags = { shiftKey: modifier === 'Shift', ctrlKey: modifier === 'Control', metaKey: modifier === 'Meta' }
    document.dispatchEvent(new KeyboardEvent('keydown', { key: modifier, code: modifier + 'Left', ...flags }))
    await nextTick()
    expect(pane.classes()).toContain('selection')
    await pane.trigger('pointerdown', { button: 0, pointerId: 1, clientX: 0, clientY: 0, ...flags })
    await pane.trigger('pointermove', { button: 0, pointerId: 1, clientX: 850, clientY: 180, ...flags })
    expect(wrapper.find('.vue-flow__selection').exists()).toBe(true)
    await pane.trigger('pointerup', { button: 0, pointerId: 1, clientX: 850, clientY: 180, ...flags })
    document.dispatchEvent(new KeyboardEvent('keyup', { key: modifier, code: modifier + 'Left' }))
    await flushPromises()
    expect(selected()).toEqual(['component:compute', 'component:source'])
    expect(flow.viewport).toEqual(viewport)
    expect(pane.classes()).not.toContain('selection')
    expect(flow.panOnDrag).toBe(true)
    expect(release).toHaveBeenCalledWith(1)
    expect(command).not.toHaveBeenCalled()
  })

  it.each(['Shift', 'Control', 'Meta'] as const)('keeps multiple components with %s + click while inspecting the latest', async modifier => {
    const { store } = await graph()
    await click('component:source')
    await click('component:compute', modifier)
    expect(selected()).toEqual(['component:compute', 'component:source'])
    expect(store.selection?.path).toEqual(['components', 'compute'])
  })

  it('groups all selected components and excludes external publishers', async () => {
    const { command, wrapper } = await graph()
    const button = wrapper.findAll('button').find(button => button.text() === 'Group selection')!
    expect(button.attributes('disabled')).toBeDefined()
    await click('component:source')
    await click('component:compute', 'Control')
    await click('external:ambient', 'Control')
    expect(wrapper.get('.graph-caption').text()).toContain('2 components selected')
    expect(button.attributes('disabled')).toBeUndefined()
    vi.stubGlobal('prompt', vi.fn(() => 'Acquisition'))
    await button.trigger('click')
    expect(command).toHaveBeenCalledWith({ action: 'workspace', groups: {
      Acquisition: { members: ['source', 'compute'], collapsed: true },
    } })
  })

  it('preserves multiple selection when labels, search and project revisions change', async () => {
    const { store, wrapper } = await graph()
    await click('component:source')
    await click('component:compute', 'Control')
    await wrapper.get('input[type="checkbox"]').setValue(false)
    await flushPromises()
    expect(selected()).toEqual(['component:compute', 'component:source'])
    await wrapper.get('input[aria-label="Find a component"]').setValue('source')
    await flushPromises()
    expect(selected()).toEqual(['component:compute', 'component:source'])
    store.project = { ...store.project, revision: 'r2' }
    await flushPromises()
    expect(selected()).toEqual(['component:compute', 'component:source'])
  })

  it('selects existing groups without expanding them and creates a second-level group', async () => {
    const { store, command, wrapper } = await graph()
    const groups = {
      one: { members: ['source'], collapsed: true },
      two: { members: ['compute'], collapsed: true },
    }
    store.project = { ...store.project, revision: 'r2', workspace: { ...store.project.workspace, groups } }
    await flushPromises()
    await click('group:one')
    await click('group:two', 'Shift')
    expect(selected()).toEqual(['group:one', 'group:two'])
    expect(command).not.toHaveBeenCalled()
    expect(wrapper.get('.graph-caption').text()).toContain('2 groups selected')
    vi.stubGlobal('prompt', vi.fn(() => 'Pipeline'))
    await wrapper.findAll('button').find(button => button.text() === 'Group selection')!.trigger('click')
    expect(command).toHaveBeenCalledWith({ action: 'workspace', groups: {
      ...groups, Pipeline: { members: [], groups: ['one', 'two'], collapsed: true },
    } })
    const updated = command.mock.calls[0][0].groups
    store.project = { ...store.project, revision: 'r3', workspace: { ...store.project.workspace, groups: updated } }
    await flushPromises()
    expect(wrapper.find('[data-id="group:one"]').exists()).toBe(false)
    expect(wrapper.find('[data-id="group:Pipeline"]').exists()).toBe(true)
    await wrapper.get('[data-id="group:Pipeline"]').trigger('dblclick')
    expect(command).toHaveBeenLastCalledWith({ action: 'workspace', groups: {
      ...groups, Pipeline: { members: [], groups: ['one', 'two'], collapsed: false },
    } })
    store.project = { ...store.project, revision: 'r4', workspace: { ...store.project.workspace, groups: command.mock.calls[1][0].groups } }
    await flushPromises()
    expect(wrapper.find('[data-id="group:one"]').exists()).toBe(true)
    expect(wrapper.find('[data-id="group:two"]').exists()).toBe(true)
    await wrapper.findAll('button').find(button => button.text() === 'Collapse all groups')!.trigger('click')
    expect(command).toHaveBeenLastCalledWith({ action: 'workspace', groups: {
      ...groups, Pipeline: { members: [], groups: ['one', 'two'], collapsed: true },
    } })
    store.project = { ...store.project, revision: 'r5', workspace: { ...store.project.workspace, groups: command.mock.calls[2][0].groups } }
    await flushPromises()
    const expandAll = wrapper.findAll('button').find(button => button.text() === 'Expand all groups')!
    expect(expandAll.attributes('disabled')).toBeUndefined()
    await expandAll.trigger('click')
    expect(command).toHaveBeenLastCalledWith({ action: 'workspace', groups: {
      one: { members: ['source'], collapsed: false },
      two: { members: ['compute'], collapsed: false },
      Pipeline: { members: [], groups: ['one', 'two'], collapsed: false },
    } })
    store.project = { ...store.project, revision: 'r6', workspace: { ...store.project.workspace, groups: command.mock.calls[3][0].groups } }
    await flushPromises()
    expect(wrapper.find('[data-id="component:source"]').exists()).toBe(true)
    expect(wrapper.find('[data-id="component:compute"]').exists()).toBe(true)
    expect(wrapper.findAll('button').some(button => button.text() === 'Collapse all groups')).toBe(true)
  })

  it('moves selected components and groups out of their former parents when regrouping', async () => {
    const { store, command, wrapper } = await graph()
    store.project = { ...store.project, revision: 'r2', workspace: { ...store.project.workspace, groups: {
      parent: { members: ['source', 'view'], groups: ['child'], collapsed: false },
      child: { members: ['compute'], collapsed: true },
    } } }
    await flushPromises()
    await click('component:source')
    await click('group:child', 'Control')
    vi.stubGlobal('prompt', vi.fn(() => 'New parent'))
    await wrapper.findAll('button').find(button => button.text() === 'Group selection')!.trigger('click')
    expect(command).toHaveBeenCalledWith({ action: 'workspace', groups: {
      parent: { members: ['view'], groups: [], collapsed: false },
      child: { members: ['compute'], collapsed: true },
      'New parent': { members: ['source'], groups: ['child'], collapsed: true },
    } })
  })

  it('rejects a duplicate group name without replacing an existing group', async () => {
    const { store, command, wrapper } = await graph()
    store.project.workspace.groups = { Existing: { members: ['compute'], collapsed: true } }
    store.project = { ...store.project, revision: 'r2' }
    await flushPromises()
    await click('component:source')
    vi.stubGlobal('prompt', vi.fn(() => 'Existing'))
    await wrapper.findAll('button').find(button => button.text() === 'Group selection')!.trigger('click')
    expect(command).not.toHaveBeenCalled()
    expect(store.error).toBe('A visual group with this name already exists.')
  })

  it('removes a node on another modifier click and selects a new node on plain click', async () => {
    await graph()
    await click('component:source')
    await click('component:compute', 'Control')
    await click('component:source', 'Control')
    expect(selected()).toEqual(['component:compute'])
    await click('component:view')
    expect(selected()).toEqual(['component:view'])
  })

  it('changes link rendering while preserving endpoints, selection and the project', async () => {
    const { store, command, wrapper } = await graph()
    const model = store.project.documents['model.json'].value
    model.components.source.outputs = { out: { root: 'Reading' } }
    model.components.compute.inputs = { input: { root: 'Reading' } }
    model.subscriptions = [{ id: 'readings', from: 'source.out', to: 'compute.input' }]
    store.project = { ...store.project, revision: 'r2' }
    await flushPromises()
    await click('component:source')
    await click('component:compute', 'Control')
    const before = JSON.stringify(store.project)
    const style = wrapper.findAllComponents(Select).find(select => select.props('optionValue') === 'value')!
    const flow = wrapper.getComponent(VueFlow)
    expect(flow.props('edges')[0].type).toBe('default')
    for (const type of ['straight', 'smoothstep', 'default']) {
      style.vm.$emit('update:modelValue', type)
      await flushPromises()
      expect(flow.props('edges')[0]).toMatchObject({ type, sourceHandle: 'source.out', targetHandle: 'compute.input', label: 'readings', markerEnd: 'arrowclosed' })
      expect(selected()).toEqual(['component:compute', 'component:source'])
    }
    expect(JSON.stringify(store.project)).toBe(before)
    expect(command).not.toHaveBeenCalled()
  })

  it('follows an explicit inspector selection from outside the graph', async () => {
    const { store } = await graph()
    await click('component:source')
    await click('component:compute', 'Control')
    store.select('model.json', ['components', 'view'])
    await flushPromises()
    expect(selected()).toEqual(['component:view'])
  })
})


it('reveals the selected component through its collapsed group without changing layout metadata', async () => {
  const { store, wrapper, command } = await graph()
  store.project.workspace.groups = { folded: { members: ['source', 'compute'], collapsed: true } }
  await flushPromises()
  const before = JSON.stringify(store.project.workspace)
  store.select('model.json', ['components', 'compute'])
  await wrapper.setProps({ navigationRequest: 1 }); await flushPromises()
  expect(selected()).toEqual(['group:folded'])
  expect(JSON.stringify(store.project.workspace)).toBe(before)
  expect(command).not.toHaveBeenCalled()
})
