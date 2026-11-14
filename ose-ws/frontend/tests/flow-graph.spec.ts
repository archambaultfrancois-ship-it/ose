import { afterEach, expect, it } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import { layout, inputSummary, inputDetails, nodeHeight, nodeWidth, type Topology } from '../src/flow-graph'
import ComponentsPage from '../src/components/ComponentsPage.vue'
import model from '../../../vibration-watch/model.json'
import { portY } from '../src/flow-graph'

const topology: Topology = {
  version: 1, components: model.components,
  subscriptions: Object.fromEntries(model.subscriptions.map(sub => [sub.id, { ...sub, policy: sub.qos }])),
}

it('lays out the real vibration model in producer order without overlapping nodes', () => {
  const graph = layout(topology)
  expect(graph.nodes).toHaveLength(Object.keys(model.components).length)
  expect(graph.edges).toHaveLength(model.subscriptions.length)
  for (const edge of graph.edges) {
    const source = graph.nodes.find(node => node.id === edge.source)!
    const target = graph.nodes.find(node => node.id === edge.target)!
    expect(source.x + nodeWidth).toBeLessThan(target.x)
    expect(edge.path).not.toContain('NaN')
  }
  for (const a of graph.nodes) for (const b of graph.nodes) {
    if (a !== b) expect(Math.abs(a.x - b.x) >= nodeWidth || Math.abs(a.y - b.y) >= nodeHeight).toBe(true)
  }
  const signal = graph.edges.find(edge => edge.id === 'signal_1')!
  expect(signal.root).toBe('SignalBlock')
  expect(signal.policy?.capacity).toBe(4)
  expect(layout(JSON.parse(JSON.stringify(topology)))).toEqual(graph)
})

it('preserves isolated nodes, parallel links, direct routes and external topics with component prefixes', () => {
  const graph = layout({ version: 1, components: {
    source: { outputs: { value: { root: 'Value' } } }, sink: {}, isolated: {},
  }, external_topics: { 'source.external': { root: 'Offset' }, unused: { root: 'Other' } }, subscriptions: {
    first: { from: 'source.value', to: 'sink.a', routing: 'direct' },
    second: { from: 'source.value', to: 'sink.b' },
    external: { from: 'source.external', from_topic: 'source.external', to: 'sink.c' },
  } })
  expect(graph.nodes).toHaveLength(5)
  expect(new Set(graph.edges.map(edge => edge.path)).size).toBe(3)
  expect(graph.edges.find(edge => edge.id === 'external')).toMatchObject({ source: 'external:source.external', root: 'Offset' })
  expect(graph.edges.find(edge => edge.id === 'first')?.routing).toBe('direct')
  expect(layout({ version: 1, components: { sink: {} }, subscriptions: {
    legacy: { from: 'outside.offset', to: 'sink.offset' },
  } }).nodes.some(node => node.name === 'outside.offset' && node.kind === 'external')).toBe(true)
  expect(layout(null).nodes).toEqual([])
})

it('describes independent activations and keeps passive feedback links visible', () => {
  const inputs = {
    clock: { kind: 'timer', timer: { mode: 'one_shot', delay: .1, start: 'manual' }, activates: { timeout: ['value'] } },
    commands: { mode: 'push', timeout: 1, activates: { message: ['value'], timeout: [] } },
    feedback: { mode: 'pull' },
  }
  const graph = layout({ version: 1, components: { a: { inputs }, b: {} }, subscriptions: {
    forward: { from: 'a.value', to: 'b.input' }, back: { from: 'b.value', to: 'a.feedback' },
  } })
  expect(inputSummary(graph.nodes.find(node => node.name === 'a')?.inputs)).toBe('1 timer · 1 Push · 1 Pull')
  expect(inputDetails(inputs)).toContain('clock: One-shot timer · 0.1 s · manual · timeout → value')
  expect(inputDetails(inputs)).toContain('commands: Push · inactivity 1 s · message → value · timeout → no outputs')
  expect(inputDetails(inputs)).toContain('feedback: Pull')
  expect(graph.edges).toHaveLength(2)
  expect(graph.edges.every(edge => !edge.path.includes('NaN'))).toBe(true)
  expect(graph.nodes[0].y).not.toBe(graph.nodes[1].y)
  expect(inputSummary({})).toBe('No activating inputs')
  expect(inputSummary()).toBe('Inputs unavailable')
})

let app: ReturnType<typeof createApp> | undefined
afterEach(() => { app?.unmount(); document.body.innerHTML = '' })

function report(state = 'ACTIVE', error: string | null = null) {
  return { topology, error, components: Object.keys(topology.components).map(name => ({
    name, state, severity: error ? 'error' : 'ok', rates: { in: 1, out: 2, ticks: 3 },
    generation: 'generation-1', queues: [], alerts: [], counters: {},
  })) }
}

it('opens diagnostics from graph nodes and updates or freezes graph and details together', async () => {
  const value = ref(report())
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(ComponentsPage, { report: value.value }) }); app.mount(root)
  expect(root.querySelectorAll('.flow-node')).toHaveLength(Object.keys(model.components).length)
  expect(root.querySelectorAll('.flow-edge')).toHaveLength(model.subscriptions.length)
  expect(root.querySelector('.flow-edge title')?.textContent).toContain('→')
  expect(root.textContent).not.toContain('Trigger unavailable')
  expect(root.querySelector('[aria-label="sensor_1: ACTIVE"] title')?.textContent).toContain('timer')
  const node = root.querySelector('[aria-label="sensor_1: ACTIVE"]')!
  node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  await nextTick()
  expect(root.querySelector('.detail h2')?.textContent).toBe('sensor_1')
  value.value = report('ATTENTION')
  await nextTick()
  expect(root.querySelector('.flow-node.selected .flow-state')?.textContent).toBe('ATTENTION')
  const freeze = [...root.querySelectorAll('button')].find(button => button.textContent === 'Freeze view')!
  freeze.click(); await nextTick()
  value.value = report('UNAVAILABLE', 'Disconnected')
  await nextTick()
  expect(root.querySelector('.flow-node.selected .flow-state')?.textContent).toBe('ATTENTION')
  expect(root.textContent).not.toContain('Last known topology')
  freeze.click(); await nextTick()
  expect(root.querySelector('.flow-node.selected .flow-state')?.textContent).toBe('UNAVAILABLE')
  expect(root.textContent).toContain('Last known topology')
  expect(root.querySelector('.flow-node.selected')?.getAttribute('data-state')).toBe('error')
  const search = root.querySelector('input[placeholder="Name contains…"]') as HTMLInputElement
  search.value = 'sensor_1'; search.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
  expect(root.querySelectorAll('.flow-node:not(.dimmed)')).toHaveLength(1)
  expect(root.querySelectorAll('tbody tr')).toHaveLength(1)
  ;([...root.querySelectorAll('button')].find(button => button.textContent === 'Close details')!).click()
  await nextTick()
  expect(root.querySelector('.detail')).toBeNull()
})

it('keeps the status table usable when topology metadata is unavailable', () => {
  const root = document.createElement('div'); document.body.append(root)
  app = createApp(ComponentsPage, { report: { ...report(), topology: null } }); app.mount(root)
  expect(root.textContent).toContain('Flow topology is unavailable')
  expect(root.querySelectorAll('tbody tr')).toHaveLength(Object.keys(model.components).length)
})

it('anchors links to named ports with enough space for all inputs and outputs', () => {
  const graph = layout(topology, true)
  for (const edge of graph.edges) {
    const source = graph.nodes.find(node => node.id === edge.source)!
    const target = graph.nodes.find(node => node.id === edge.target)!
    const output = Object.keys(source.outputs || {}).indexOf(edge.from.split('.')[1])
    const input = Object.keys(target.inputs || {}).indexOf(edge.to.split('.')[1])
    expect(output).toBeGreaterThanOrEqual(0)
    expect(input).toBeGreaterThanOrEqual(0)
    expect(edge.path).toContain(`M ${source.x + nodeWidth} ${source.y + portY(output)} `)
    expect(edge.path.endsWith(`${target.x} ${target.y + portY(input)}`)).toBe(true)
    expect(portY(input)).toBeLessThan(target.height)
  }
  for (const a of graph.nodes) for (const b of graph.nodes) {
    if (a !== b && a.x === b.x) expect(a.y + a.height <= b.y || b.y + b.height <= a.y).toBe(true)
  }
})

it('toggles ports and history, changes metrics, and freezes historical values with current values', async () => {
  const snapshot = (second: number, cpu: number) => ({ ...report(), epoch: 'e1', timestamp: `2026-11-14T10:00:0${second}Z`,
    components: report().components.map(row => ({ ...row, resources: { component: { cpu_percent: cpu, rss_bytes: 2097152 } } })) })
  const value = ref(snapshot(0, 12))
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(ComponentsPage, { report: value.value, active: true }) }); app.mount(root)
  const toggle = async (label: string) => {
    const input = [...root.querySelectorAll('label')].find(el => el.textContent?.trim() === label)!.querySelector('input')!
    input.click(); await nextTick()
  }
  expect(root.querySelector('.flow-port')).toBeNull()
  await toggle('Ports')
  expect(root.querySelectorAll('.flow-port').length).toBeGreaterThan(0)
  expect(root.querySelector('.flow-port.timer title')?.textContent).toContain('timer')
  const metric = root.querySelector('.flow-panel select') as HTMLSelectElement
  metric.value = 'memory'; metric.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
  expect(root.querySelector('.flow-metric')?.textContent).toBe('2 MiB')
  const points = () => root.querySelectorAll('.flow-history circle').length
  const initial = points()
  expect(initial).toBe(Object.keys(topology.components).length)
  const freeze = [...root.querySelectorAll('button')].find(button => button.textContent === 'Freeze view')!
  freeze.click(); await nextTick()
  value.value = snapshot(1, 99); await nextTick()
  expect(points()).toBe(initial)
  freeze.click(); await nextTick()
  expect(points()).toBe(initial * 2)
  await toggle('History')
  expect(root.querySelector('.flow-history')).toBeNull()
  await toggle('Ports')
  expect(root.querySelector('.flow-port')).toBeNull()
  const states = [...root.querySelectorAll('.toolbar select option')].map(option => option.textContent)
  for (const state of ['DISABLED', 'ENABLING', 'DISABLING', 'WAIT PUB']) expect(states).toContain(state)
})

it('opens full link details by tapping a line or label, without a hover event', async () => {
  const value = ref(report())
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(ComponentsPage, { report: value.value }) }); app.mount(root)
  const signal = root.querySelector('[aria-label^="Subscription signal_1:"]')!
  expect(root.querySelector('.flow-link-details')).toBeNull()
  signal.querySelector('.flow-hit')!.dispatchEvent(new Event('click', { bubbles: true }))
  await nextTick()
  const details = () => root.querySelector('.flow-link-details')!
  expect(signal.getAttribute('aria-expanded')).toBe('true')
  const fields = () => Object.fromEntries([...details().querySelectorAll('dl>div')].map(row => [
    row.querySelector('dt')!.textContent, row.querySelector('dd')!.textContent,
  ]))
  expect(fields()).toMatchObject({ From: 'sensor_1.signal', To: 'analyzer_1.input',
    'SDL type': 'SignalBlock', Buffer: 'fifo', Capacity: '4', Overflow: 'drop_oldest', Acknowledgement: 'received' })
  value.value = report()
  await nextTick()
  expect(details().querySelector('code')?.textContent).toBe('signal_1')
  ;(details().querySelector('button') as HTMLButtonElement).click()
  await nextTick()
  expect(root.querySelector('.flow-link-details')).toBeNull()
  expect(signal.getAttribute('aria-expanded')).toBe('false')
  const analysis = root.querySelector('[aria-label^="Subscription analysis_1:"]')!
  analysis.querySelector('text')!.dispatchEvent(new Event('click', { bubbles: true }))
  await nextTick()
  expect(fields()).toMatchObject({ From: 'analyzer_1.result', To: 'alarm.machine_1', 'SDL type': 'Analysis' })
  const events = root.querySelector('[aria-label^="Subscription events:"]')!
  events.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  await nextTick()
  expect(fields()).toMatchObject({ From: 'alarm.event', To: 'observer.input', 'SDL type': 'Alarm' })
  details().querySelector('button')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  await nextTick()
  expect(root.querySelector('.flow-link-details')).toBeNull()
})
