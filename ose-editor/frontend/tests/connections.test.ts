// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { VueFlow } from '@vue-flow/core'
import GraphView from '../src/components/GraphView.vue'
import { assessConnection, publishers } from '../src/connections'
import { useProject } from '../src/store'

function project() {
  return { revision: 'r1', model: 'model.json', documents: { 'model.json': { value: {
    schemas: 'schemas', components: {
      source: { inputs: { feedback: { root: 'Reading' } }, outputs: { out: { root: 'Reading' } } },
      compute: { inputs: { input: { root: 'Reading', schema: 'consumer' } }, outputs: { out: { root: 'Reading' } } },
      wrong: { inputs: { input: { root: 'Other' } }, outputs: {} },
    }, external_topics: { 'ambient.topic': { root: 'Reading' } },
    subscriptions: [{ id: 'existing', from: 'source.out', to: 'compute.input' }],
  } } }, workspace: { positions: {}, groups: {}, pinned: [] }, validation: null as any }
}
const source = { endpoint: 'source.out', external: false }
function report(compatible = true) {
  return { root: 'Reading', publisher_schema: 'schemas', consumer_schema: 'consumer', compatible,
    code: 'missing_required', path: 'Reading/2', message: 'Required field 2 is missing' }
}

describe('connection guidance', () => {
  it('rejects missing ports, different roots and direct or transitive cycles', () => {
    const state = project()
    expect(assessConnection(state, source, 'missing.input').status).toBe('incompatible')
    expect(assessConnection(state, source, 'wrong.input').message).toContain('Different logical roots')
    expect(assessConnection(state, source, 'source.feedback').message).toContain('cycle')
    expect(assessConnection(state, { endpoint: 'compute.out', external: false }, 'source.feedback').message).toContain('cycle')
  })
  it('allows fan-in and dotted external topics, with explicit unknown status before validation', () => {
    const state = project(), before = JSON.stringify(state)
    expect(assessConnection(state, source, 'compute.input').status).toBe('unknown')
    expect(assessConnection(state, { endpoint: 'ambient.topic', external: true }, 'compute.input').status).toBe('unknown')
    expect(publishers(state.documents['model.json'].value)).toContainEqual({ endpoint: 'ambient.topic', external: true })
    expect(JSON.stringify(state)).toBe(before)
  })
  it('uses directional, scoped SDL reports only for their validated revision', () => {
    const state = project()
    state.validation = { revision: 'r1', connection_contracts: [report(false)] }
    expect(assessConnection(state, source, 'compute.input')).toMatchObject({ status: 'incompatible', message: expect.stringContaining('Reading/2') })
    state.validation.connection_contracts = [report()]
    expect(assessConnection(state, source, 'compute.input').status).toBe('compatible')
    // The reverse schema direction was not checked.
    state.validation.connection_contracts = [{ ...report(), publisher_schema: 'consumer', consumer_schema: 'schemas' }]
    expect(assessConnection(state, source, 'compute.input').status).toBe('unknown')
    state.validation = { revision: 'old', connection_contracts: [report(false)] }
    expect(assessConnection(state, source, 'compute.input').status).toBe('unknown')
  })
  it('rejects stale model projections and conflicting shared reception', () => {
    const state: any = project()
    state.documents['model.json'].stale = true
    expect(assessConnection(state, source, 'compute.input').message).toContain('Repair the model')
    state.documents['model.json'].stale = false
    state.documents['model.json'].value.subscriptions.push({ id: 'other', from_topic: 'ambient.topic', to: 'compute.input', qos: { buffer: 'latest' } })
    expect(assessConnection(state, source, 'compute.input').message).toContain('conflicting reception')
  })
})

let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function graph() {
  const pinia = createPinia(); setActivePinia(pinia)
  vi.stubGlobal('Worker', class { onmessage: any; postMessage() {} terminate() {} })
  const store = useProject(); store.project = project(); store.selection = { file: 'model.json', path: [] }
  const command = vi.spyOn(store, 'command').mockResolvedValue({ id: 'preview' })
  wrapper = mount(GraphView, { attachTo: document.body, global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
  await flushPromises()
  return { store, command, flow: wrapper.findComponent(VueFlow) }
}

describe('assisted graph connections', () => {
  it('marks drag destinations and explains rejected ports without creating commands', async () => {
    const { flow, command, store } = await graph()
    store.project.validation = { revision: 'r1', connection_contracts: [report()] }
    flow.vm.$emit('connectStart', { nodeId: 'component:source', handleId: 'source.out', handleType: 'source' })
    await flushPromises()
    const target = wrapper!.get('[data-id="component:compute"] .node-port.input')
    expect(target.attributes('data-connection-status')).toBe('compatible')
    const wrong = wrapper!.get('[data-id="component:wrong"] .node-port.input')
    expect(wrong.text()).toContain('× Unavailable')
    await wrong.trigger('pointerenter')
    expect(wrapper!.get('.connection-feedback').text()).toContain('Different logical roots')
    const valid = flow.props('isValidConnection')!
    expect(valid({ source: 'component:source', sourceHandle: 'source.out', target: 'component:wrong', targetHandle: 'wrong.input' } as any, {} as any)).toBe(false)
    expect(command).not.toHaveBeenCalled()
    flow.vm.$emit('connectEnd', undefined)
    await flushPromises()
    expect(wrapper!.find('[data-connection-status]').exists()).toBe(false)
  })
  it('supports reverse drags and real endpoints on collapsed groups', async () => {
    const { store, flow } = await graph()
    store.project.workspace.groups = { pair: { members: ['source', 'compute'], collapsed: true } }
    store.project.revision = 'r2'
    await flushPromises()
    flow.vm.$emit('connectStart', { nodeId: 'group:pair', handleId: 'source.feedback', handleType: 'target' })
    await flushPromises()
    expect(wrapper!.get('[data-id="group:pair"] .node-port.output').attributes('data-connection-status')).toBe('incompatible')
    expect(wrapper!.get('[data-id="external:ambient.topic"] .node-port.output').attributes('data-connection-status')).toBe('unknown')
  })
  it('creates a preview from native keyboard-accessible controls and keeps existing fan-in', async () => {
    const { command } = await graph()
    await wrapper!.findAll('button').find(button => button.text() === 'Connect ports')!.trigger('click')
    const publisher = wrapper!.get('select[aria-label="Connection publisher"]')
    expect(document.activeElement).toBe(publisher.element)
    await publisher.setValue(JSON.stringify([true, 'ambient.topic']))
    await wrapper!.get('select[aria-label="Connection destination"]').setValue('compute.input')
    expect(wrapper!.get('#connection-assessment').text()).toContain('Contract unchecked')
    await wrapper!.get('input[aria-label="Connection subscription ID"]').setValue('outside')
    await wrapper!.get('form[aria-label="Connect ports"]').trigger('submit')
    expect(command).toHaveBeenCalledWith({ action: 'connect', name: 'outside', from: 'ambient.topic', to: 'compute.input', external: true })
  })
  it('opens the same review path after a valid drag and disables incompatible selections', async () => {
    const { flow, command, store } = await graph()
    flow.vm.$emit('connect', { source: 'component:source', sourceHandle: 'source.out', target: 'component:compute', targetHandle: 'compute.input' })
    await flushPromises()
    expect(document.activeElement).toBe(wrapper!.get('input[aria-label="Connection subscription ID"]').element)
    expect(command).not.toHaveBeenCalled()
    store.project.validation = { revision: 'r1', connection_contracts: [report(false)] }
    await flushPromises()
    await wrapper!.get('input[aria-label="Connection subscription ID"]').setValue('new_link')
    const submit = wrapper!.findAll('button').find(button => button.text() === 'Preview connection')!
    expect(submit.attributes('disabled')).toBeDefined()
    expect(wrapper!.get('#connection-assessment').text()).toContain('Required field 2 is missing')
    await wrapper!.get('form').trigger('submit')
    expect(command).not.toHaveBeenCalled()
  })
})
