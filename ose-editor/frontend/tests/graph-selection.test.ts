// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
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

  it('removes a node on another modifier click and selects a new node on plain click', async () => {
    await graph()
    await click('component:source')
    await click('component:compute', 'Control')
    await click('component:source', 'Control')
    expect(selected()).toEqual(['component:compute'])
    await click('component:view')
    expect(selected()).toEqual(['component:view'])
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
