import { afterEach, expect, it } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import { layout, nodeHeight, nodeWidth, type Topology } from '../src/flow-graph'
import ComponentsPage from '../src/components/ComponentsPage.vue'
import model from '../../../vibration-watch/model.json'

const topology: Topology = {
  version: 1, components: model.components,
  subscriptions: Object.fromEntries(model.subscriptions.map(sub => [sub.id, { ...sub, policy: sub.qos }])),
}

it('lays out the real vibration model in producer order without overlapping nodes', () => {
  const graph = layout(topology)
  expect(graph.nodes).toHaveLength(6)
  expect(graph.edges).toHaveLength(5)
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
  expect(root.querySelectorAll('.flow-node')).toHaveLength(6)
  expect(root.querySelectorAll('.flow-edge')).toHaveLength(5)
  expect(root.querySelector('.flow-edge title')?.textContent).toContain('→')
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
  expect(root.querySelectorAll('tbody tr')).toHaveLength(6)
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
