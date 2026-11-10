// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { VueFlow } from '@vue-flow/core'
import { topology } from '../src/graph'
import { activationCycle } from '../src/execution'
import { assessConnection, inputs } from '../src/connections'
import { keysFor, choicesFor } from '../src/hints'
import { useProject } from '../src/store'
import ExecutionView from '../src/components/ExecutionView.vue'
import GraphView from '../src/components/GraphView.vue'
import ReceptionView from '../src/components/ReceptionView.vue'

function model() {
  return { version: 2, schemas: 'schemas', components: {
    a: { inputs: {
      clock: { kind: 'timer', timer: { mode: 'periodic', delay: { $number: '0.1' } }, activates: { timeout: ['out'] } },
      feedback: { root: 'Value', mode: 'pull' },
    }, outputs: { out: { root: 'Value' } } },
    b: { inputs: { input: { root: 'Value', activates: { message: ['out'] } } }, outputs: { out: { root: 'Value' } } },
  }, subscriptions: [{ id: 'forward', from: 'a.out', to: 'b.input' }, { id: 'return', from: 'b.out', to: 'a.feedback' }] }
}
let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function setup(component: any = ExecutionView, port = 'feedback') {
  const pinia = createPinia(); setActivePinia(pinia)
  vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
  const store = useProject()
  store.project = { revision: 'one', model: 'model.json', documents: { 'model.json': { value: model() } },
    workspace: { positions: {}, groups: {}, pinned: [] }, validation: null }
  store.selection = { file: 'model.json', path: ['components', 'a', 'inputs', port] }
  const command = vi.spyOn(store, 'command').mockResolvedValue({ id: 'preview' })
  wrapper = mount(component, { attachTo: document.body, global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
  await flushPromises()
  return { store, command, wrapper }
}

describe('v2 activation graph guidance', () => {
  it('accepts feedback and explains when activating message cycles require microsteps', () => {
    const state: any = { model: 'model.json', documents: { 'model.json': { value: model() } } }
    expect(activationCycle(state.documents['model.json'].value)).toBeNull()
    const source = { endpoint: 'b.out', external: false }
    expect(assessConnection(state, source, 'a.feedback').status).toBe('unknown')
    const feedback = state.documents['model.json'].value.components.a.inputs.feedback as any
    feedback.mode = 'push'
    expect(assessConnection(state, source, 'a.feedback').status).toBe('unknown')
    feedback.timeout = .1; feedback.activates = { timeout: ['out'] }
    expect(assessConnection(state, source, 'a.feedback').status).toBe('unknown')
    feedback.activates.message = ['out']
    expect(assessConnection(state, source, 'a.feedback').status).toBe('unknown')
    expect(assessConnection(state, source, 'a.feedback').message).toContain('--microsteps')
    expect(assessConnection(state, source, 'a.feedback').message).toContain('a.feedback:message')
    expect(assessConnection(state, source, 'a.feedback').message).toContain('activation cycle')
  })
  it('excludes timers from connectable inputs and refuses direct timer connection attempts', () => {
    const value = model(), state = { model: 'model.json', documents: { 'model.json': { value } } }
    expect(inputs(value)).toEqual(['a.feedback', 'b.input'])
    expect(assessConnection(state, { endpoint: 'b.out', external: false }, 'a.clock').message).toContain('Timer inputs')
  })
  it('handles compiled external Push subscriptions and long activation chains without recursion', () => {
    const value: any = model()
    value.subscriptions = [{ from: 'external.signal', from_topic: 'external.signal', to: 'b.input' }]
    expect(activationCycle(value)).toBeNull()
    value.components = {}; value.subscriptions = []
    for (let i = 0; i < 2500; i++) {
      value.components['n' + i] = { inputs: { input: { activates: { message: ['out'] } } }, outputs: { out: {} } }
      if (i) value.subscriptions.push({ from: 'n' + (i - 1) + '.out', to: 'n' + i + '.input' })
    }
    expect(activationCycle(value)).toBeNull()
    value.subscriptions.push({ from: 'n2499.out', to: 'n0.input' })
    expect(activationCycle(value)?.length).toBe(5001)
  })
  it('projects input modes and optional internal arrows without mutating model or workspace', () => {
    const value = model(), workspace = {}, before = JSON.stringify(value)
    const hidden = topology(value, workspace), shown = topology(value, workspace, true, 'default', true)
    expect(hidden.nodes[0].data.showActivations).toBe(false)
    expect(shown.nodes[0].data.activations).toEqual([{ input: 'clock', event: 'timeout', output: 'out' }])
    expect(shown.nodes[0].data.inputs[0]).toMatchObject({ mode: 'timer', connectable: false })
    expect(shown.nodes[0].data.inputs[1]).toMatchObject({ mode: 'pull', connectable: true })
    expect(shown.edges).toEqual(hidden.edges)
    expect(JSON.stringify(value)).toBe(before)
  })
  it('offers version-specific timer, mode and activation hints without legacy required fields', () => {
    const value = model()
    expect(keysFor('model', [], value).version).toBe('2')
    expect(keysFor('model', ['components', 'a'], value)).not.toHaveProperty('trigger')
    expect(keysFor('model', ['components', 'a', 'outputs', 'out'], value)).not.toHaveProperty('required')
    expect(keysFor('model', ['components', 'a', 'inputs', 'clock'], value)).not.toHaveProperty('root')
    expect(keysFor('model', ['components', 'a', 'inputs', 'feedback'], value)).not.toHaveProperty('timeout')
    expect(choicesFor('model', ['components', 'a', 'inputs', 'clock', 'timer', 'mode'], value)).toEqual(['periodic', 'one_shot'])
    expect(choicesFor('model', ['components', 'a', 'inputs', 'feedback', 'mode'], value)).toEqual(['push', 'pull'])
    expect(keysFor('deployment', [], value).version).toBe('1')
  })
})

describe('input execution inspector', () => {
  it('keeps Pull passive and submits an explicit empty message permission list when changing to Push', async () => {
    const { store, command, wrapper } = await setup()
    const before = JSON.stringify(store.project)
    expect(wrapper.text()).toContain('Passive Pull has no callback')
    expect(wrapper.find('[aria-label="Allow message to out"]').exists()).toBe(false)
    await wrapper.get('[aria-label="Input mode"]').setValue('push')
    expect(wrapper.find('[aria-label="Allow message to out"]').exists()).toBe(true)
    await wrapper.get('form').trigger('submit')
    expect(command).toHaveBeenCalledWith({ action: 'configure_input', component: 'a', port: 'feedback',
      description: { root: 'Value', mode: 'push', activates: { message: [] } } })
    expect(JSON.stringify(store.project)).toBe(before)
  })
  it('allows an activating cycle with microstep guidance before creating a preview', async () => {
    const { command, wrapper } = await setup()
    await wrapper.get('[aria-label="Input mode"]').setValue('push')
    await wrapper.get('[aria-label="Allow message to out"]').setValue(true)
    expect(wrapper.text()).toContain('Activation cycle:')
    expect(wrapper.text()).toContain('--microsteps')
    expect(wrapper.findAll('button').find(button => button.text() === 'Preview input changes')!.attributes('disabled')).toBeUndefined()
    await wrapper.get('form').trigger('submit')
    expect(command).toHaveBeenCalledWith({ action: 'configure_input', component: 'a', port: 'feedback',
      description: { root: 'Value', mode: 'push', activates: { message: ['out'] } } })
  })
  it('edits message and timeout permissions independently', async () => {
    const { command, wrapper } = await setup()
    await wrapper.get('[aria-label="Input mode"]').setValue('push')
    await wrapper.get('[aria-label="Enable inactivity timeout"]').setValue(true)
    await wrapper.get('[aria-label="Input inactivity timeout"]').setValue('0.25')
    await wrapper.get('[aria-label="Allow timeout to out"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    expect(command).toHaveBeenCalledWith({ action: 'configure_input', component: 'a', port: 'feedback',
      description: { root: 'Value', mode: 'push', timeout: .25, activates: { message: [], timeout: ['out'] } } })
  })
  it('removes timeout and activation properties explicitly in a Pull preview', async () => {
    const { store, command, wrapper } = await setup()
    store.project.documents['model.json'].value.components.a.inputs.feedback = {
      root: 'Value', timeout: .2, activates: { timeout: ['out'] } }
    store.project = { ...store.project, revision: 'two' }
    await flushPromises()
    await wrapper.get('[aria-label="Input mode"]').setValue('pull')
    await wrapper.get('form').trigger('submit')
    expect(command).toHaveBeenCalledWith({ action: 'configure_input', component: 'a', port: 'feedback',
      description: { root: 'Value', mode: 'pull' } })
  })
  it('edits timer mode, delay and arm state without introducing an SDL message', async () => {
    const { command, wrapper } = await setup(ExecutionView, 'clock')
    expect(wrapper.find('[aria-label="Input logical root"]').exists()).toBe(false)
    await wrapper.get('[aria-label="Timer mode"]').setValue('one_shot')
    await wrapper.get('[aria-label="Timer delay"]').setValue('0.025')
    await wrapper.get('[aria-label="Timer initial state"]').setValue('manual')
    await wrapper.get('form').trigger('submit')
    expect(command).toHaveBeenCalledWith({ action: 'configure_input', component: 'a', port: 'clock',
      description: { kind: 'timer', timer: { mode: 'one_shot', delay: .025, start: 'manual' }, activates: { timeout: ['out'] } } })
  })
  it('rejects connected timers and invalid delays, and disables pending edits', async () => {
    const { store, command, wrapper } = await setup()
    await wrapper.get('[aria-label="Input kind"]').setValue('timer')
    expect(wrapper.text()).toContain('Disconnect incoming subscriptions')
    await wrapper.get('form').trigger('submit')
    expect(command).not.toHaveBeenCalled()
    store.select('model.json', ['components', 'a', 'inputs', 'clock'])
    await flushPromises()
    await wrapper.get('[aria-label="Timer delay"]').setValue('0')
    expect(wrapper.text()).toContain('positive seconds')
    await wrapper.get('form').trigger('submit')
    expect(command).not.toHaveBeenCalled()
    store.pending = { id: 'pending' }
    await flushPromises()
    expect(wrapper.get('form > fieldset').attributes('disabled')).toBeDefined()
  })
  it('adds a new named input through the same compound preview', async () => {
    const { command, wrapper } = await setup()
    await wrapper.findAll('button').find(button => button.text() === 'Add input')!.trigger('click')
    await wrapper.get('[aria-label="New input name"]').setValue('watchdog')
    await wrapper.get('[aria-label="Input kind"]').setValue('timer')
    await wrapper.get('form').trigger('submit')
    expect(command).toHaveBeenCalledWith({ action: 'configure_input', component: 'a', port: 'watchdog',
      description: { kind: 'timer', timer: { mode: 'periodic', delay: .1, start: 'automatic' }, activates: { timeout: [] } } })
  })
  it('does not show reception-buffer controls for a timer', async () => {
    const { wrapper } = await setup(ReceptionView, 'clock')
    expect(wrapper.find('[aria-label="Input reception policy"]').exists()).toBe(false)
  })
})

describe('optional graph activation arrows', () => {
  it('toggles internal arrows without changing subscriptions and lets ports open their inspector', async () => {
    const { store, command, wrapper } = await setup(GraphView)
    const before = JSON.stringify(store.project)
    expect(wrapper.find('.activation-arrows').exists()).toBe(false)
    expect(wrapper.get('[data-id="component:a"]').text()).toContain('Pull · passive')
    expect(wrapper.get('[data-id="component:a"]').findAll('.vue-flow__handle.target')).toHaveLength(1)
    await wrapper.get('[aria-label="Show activation links"]').setValue(true)
    await flushPromises()
    expect(wrapper.findAll('.activation-arrows > path')).toHaveLength(2)
    expect(wrapper.get('.activation-arrows > path.timeout title').text()).toContain('clock · timeout → out')
    expect(wrapper.getComponent(VueFlow).props('edges')).toHaveLength(2)
    await wrapper.get('[aria-label="Edit input a.clock"]').trigger('click')
    expect(store.selection?.path).toEqual(['components', 'a', 'inputs', 'clock'])
    expect(JSON.stringify(store.project)).toBe(before)
    expect(command).not.toHaveBeenCalled()
  })
})

