// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { VueFlow } from '@vue-flow/core'
import { useProject } from '../src/store'
import GraphView from '../src/components/GraphView.vue'
import ComponentEditor from '../src/components/ComponentEditor.vue'
import ComponentInspector from '../src/components/ComponentInspector.vue'
let wrapper: VueWrapper | undefined
function project(): any {
  return { revision: 'one', model: 'model.json', workspace: { positions: {}, groups: {}, pinned: [] }, documents: {
    'model.json': { value: { version: 2, schemas: 'value.sdl', components: {
      a: { inputs: {
        clock: { kind: 'timer', timer: { mode: 'periodic', delay: .1 }, activates: { timeout: ['out'] } },
        active: { root: 'Value', timeout: .2, activates: { message: ['out'], timeout: [] } },
        cache: { root: 'Value', mode: 'pull' },
      }, outputs: { out: { root: 'Value' }, extra: { root: 'Value' } } },
      b: { inputs: { input: { root: 'Value' } }, outputs: {} },
    }, subscriptions: [{ id: 'forward', from: 'a.out', to: 'b.input' }] } },
  } }
}
async function setup(graph = false) {
  const pinia = createPinia(); setActivePinia(pinia)
  vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
  const store = useProject(); store.project = project()
  if (!graph) store.editComponent('a')
  const command = vi.spyOn(store, 'command').mockResolvedValue({ id: 'preview' })
  const surface = { components: { ComponentEditor, ComponentInspector }, template: '<ComponentEditor/><ComponentInspector/>' }
  wrapper = mount(graph ? GraphView : surface, { attachTo: document.body, global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
  await flushPromises()
  return { store, command, wrapper }
}
function button(label: string) { return wrapper!.findAll('button').find(button => button.text() === label)! }
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('component editor', () => {
  it('opens with a pencil, retains the global canvas, and returns to the same view', async () => {
    const { store, wrapper } = await setup(true)
    const flow = wrapper.getComponent(VueFlow).vm
    await wrapper.get('[aria-label="Find a component"]').setValue('a')
    await wrapper.get('[aria-label="Edit component a"]').trigger('click')
    expect(store.editingComponent).toBe('a')
    expect(wrapper.getComponent(ComponentEditor).exists()).toBe(true)
    expect(wrapper.get('.graph-overview').attributes('style')).toContain('display: none')
    await wrapper.get('[aria-label="Back to application graph"]').trigger('click')
    expect(store.editingComponent).toBe('')
    expect(wrapper.getComponent(VueFlow).vm).toBe(flow)
    expect((wrapper.get('[aria-label="Find a component"]').element as HTMLInputElement).value).toBe('a')
  })
  it('opens by double-click while preserving group expansion', async () => {
    const { store, command, wrapper } = await setup(true)
    const flow = wrapper.getComponent(VueFlow)
    flow.vm.$emit('nodeDoubleClick', { node: { data: { kind: 'component', name: 'a' } } })
    await flushPromises(); expect(store.editingComponent).toBe('a')
    store.closeComponent(); store.project.workspace.groups = { pair: { members: ['a', 'b'], collapsed: true } }
    await flushPromises()
    flow.vm.$emit('nodeDoubleClick', { node: { data: { kind: 'group', name: 'pair' } } })
    expect(command).toHaveBeenCalledWith({ action: 'workspace', groups: { pair: { members: ['a', 'b'], collapsed: false } } })
    expect(store.editingComponent).toBe('')
  })
  it('keeps Pull passive and exposes distinct message, inactivity and timer events', async () => {
    const { wrapper } = await setup()
    expect(wrapper.find('[aria-label="Connect cache message"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Connect clock message"]').exists()).toBe(false)
    for (const origin of ['clock timeout', 'active message', 'active timeout']) expect(wrapper.get('[aria-label="Connect ' + origin + '"]').exists()).toBe(true)
    expect(wrapper.get('[aria-label="Connections for output out"]').text()).toContain('b.input')
  })
  it('connects an input event to an output with the keyboard', async () => {
    const { command, wrapper } = await setup()
    await wrapper.get('[aria-label="Connect active timeout"]').trigger('keydown', { key: 'Enter' })
    await wrapper.get('[aria-label="Select output extra"]').trigger('keydown', { key: 'Enter' })
    expect(command).toHaveBeenCalledExactlyOnceWith({ action: 'configure_activation', component: 'a', input: 'active', event: 'timeout', output: 'extra', enabled: true })
  })
  it('drags an activation without also creating a subscription', async () => {
    const { command, wrapper } = await setup()
    await wrapper.get('[aria-label="Connect active timeout"]').trigger('pointerdown', { button: 0, clientX: 300, clientY: 250 })
    window.dispatchEvent(new PointerEvent('pointermove', { clientX: 600, clientY: 100 })); await flushPromises()
    expect(wrapper.find('.drag-link').exists()).toBe(true)
    await wrapper.get('[data-output="extra"] circle').trigger('pointerup')
    await wrapper.get('[aria-label="Select output extra"]').trigger('click')
    expect(command).toHaveBeenCalledExactlyOnceWith({ action: 'configure_activation', component: 'a', input: 'active', event: 'timeout', output: 'extra', enabled: true })
    expect(wrapper.find('.drag-link').exists()).toBe(false)
  })
  it('cancels gestures and rejects duplicate or pending links', async () => {
    const { store, command, wrapper } = await setup()
    await wrapper.get('[aria-label="Connect active timeout"]').trigger('pointerdown', { button: 0 })
    window.dispatchEvent(new PointerEvent('pointercancel'))
    await wrapper.get('[aria-label="Select output extra"]').trigger('click'); expect(command).not.toHaveBeenCalled()
    await wrapper.get('[aria-label="Connect active message"]').trigger('click')
    await wrapper.get('[aria-label="Select output out"]').trigger('click'); expect(command).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('already exists')
    store.pending = { id: 'pending' }; await flushPromises()
    await wrapper.get('[aria-label="Connect active timeout"]').trigger('click')
    await wrapper.get('[aria-label="Select output extra"]').trigger('click'); expect(command).not.toHaveBeenCalled()
    expect(button('+ Input').attributes('disabled')).toBeDefined()
  })
  it('selects, replaces and removes a link through its properties', async () => {
    const { command, wrapper } = await setup()
    await wrapper.get('[aria-label="active message to out"]').trigger('click')
    expect(wrapper.get('[aria-label="Activation input"]').findAll('option').map(option => option.text())).toEqual(['clock', 'active'])
    await wrapper.get('[aria-label="Activation event"]').setValue('timeout')
    await wrapper.get('[aria-label="Activation output"]').setValue('extra')
    await wrapper.getComponent(ComponentInspector).get('form').trigger('submit')
    expect(command).toHaveBeenLastCalledWith({ action: 'configure_activation', component: 'a', input: 'active', event: 'timeout', output: 'extra', previous: { input: 'active', event: 'message', output: 'out' }, enabled: true })
    await button('Remove activation link').trigger('click')
    expect(command).toHaveBeenLastCalledWith({ action: 'configure_activation', component: 'a', input: 'active', event: 'message', output: 'out', enabled: false })
  })
  it('adds a timer through its form without an SDL message', async () => {
    const { command, wrapper } = await setup()
    await button('+ Timer').trigger('click'); expect(wrapper.find('[aria-label="Input logical root"]').exists()).toBe(false)
    await wrapper.get('[aria-label="New input name"]').setValue('watchdog')
    await wrapper.get('[aria-label="Timer delay"]').setValue('0.25')
    await wrapper.get('form[aria-label="Configure input"]').trigger('submit')
    expect(command).toHaveBeenLastCalledWith({ action: 'configure_input', component: 'a', port: 'watchdog', create: true, description: { kind: 'timer', timer: { mode: 'periodic', delay: .25, start: 'automatic' }, activates: { timeout: [] } } })
  })
  it('adds and configures outputs with root suggestions', async () => {
    const { command, wrapper } = await setup()
    await button('+ Output').trigger('click')
    await wrapper.get('[aria-label="New output name"]').setValue('result')
    await wrapper.get('[aria-label="Output logical root"]').setValue('Value')
    expect(wrapper.find('datalist option[value="Value"]').exists()).toBe(true)
    await wrapper.get('form[aria-label="Configure output"]').trigger('submit')
    expect(command).toHaveBeenLastCalledWith({ action: 'configure_output', component: 'a', port: 'result', create: true, description: { root: 'Value' } })
  })
  it('renames, duplicates and removes a port using compound previews', async () => {
    const { command, wrapper } = await setup()
    await wrapper.get('[aria-label="Select output extra"]').trigger('click')
    await button('Rename').trigger('click'); await wrapper.get('[aria-label="New port name"]').setValue('renamed')
    await wrapper.findAll('form').find(form => form.find('[aria-label="New port name"]').exists())!.trigger('submit')
    expect(command).toHaveBeenLastCalledWith({ action: 'rename', kind: 'port', old: 'a.extra', direction: 'outputs', name: 'renamed' })
    await button('Duplicate').trigger('click')
    await wrapper.findAll('form').find(form => form.find('[aria-label="New port name"]').exists())!.trigger('submit')
    expect(command).toHaveBeenLastCalledWith({ action: 'duplicate_port', component: 'a', direction: 'outputs', source: 'extra', port: 'extra_copy' })
    await button('Remove port').trigger('click')
    expect(command).toHaveBeenLastCalledWith({ action: 'remove_port', component: 'a', direction: 'outputs', port: 'extra' })
  })
  it('tracks applied renames and a component removed by undo', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const store = useProject(); store.project = project(); store.editComponent('a')
    let next = project()
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith('commands/prepare') ? { id: 'preview', revision: 'one' } : next))))
    await store.command({ action: 'rename', kind: 'component', old: 'a', name: 'renamed' })
    next.documents['model.json'].value.components = { renamed: next.documents['model.json'].value.components.a }
    await store.apply(); expect(store.editingComponent).toBe('renamed'); expect(store.selection?.path).toEqual(['components', 'renamed'])
    await store.command({ action: 'rename', kind: 'port', old: 'renamed.extra', direction: 'outputs', name: 'result' })
    const node = next.documents['model.json'].value.components.renamed
    node.outputs.result = node.outputs.extra; delete node.outputs.extra
    await store.apply(); expect(store.selection?.path).toEqual(['components', 'renamed', 'outputs', 'result'])
    next = project(); await store.history(); expect(store.editingComponent).toBe('')
  })
})
