import { describe, expect, it } from 'vitest'
import ELK from 'elkjs/lib/elk.bundled.js'
import { topology } from '../src/graph'
import { layoutGraph, layoutPositions, resizeLayout } from '../src/layout'

describe('auto layout port order', () => {
  it('places branch 1 above branch 2 to match the visible alarm input order', async () => {
    const port = { root: 'Reading' }
    const model = { version: 2, components: {
      sensor_1: { inputs: {}, outputs: { signal: port } },
      analyzer_1: { inputs: { input: port }, outputs: { result: port } },
      sensor_2: { inputs: {}, outputs: { signal: port } },
      analyzer_2: { inputs: { input: port }, outputs: { result: port } },
      alarm: { inputs: { machine_1: port, machine_2: port }, outputs: { event: port } },
      observer: { inputs: { input: port }, outputs: {} },
    }, subscriptions: [
      { id: 'signal_1', from: 'sensor_1.signal', to: 'analyzer_1.input' },
      { id: 'analysis_1', from: 'analyzer_1.result', to: 'alarm.machine_1' },
      { id: 'signal_2', from: 'sensor_2.signal', to: 'analyzer_2.input' },
      { id: 'analysis_2', from: 'analyzer_2.result', to: 'alarm.machine_2' },
      { id: 'events', from: 'alarm.event', to: 'observer.input' },
    ] }
    const { nodes, edges } = topology(model, {})
    const result = await new ELK().layout(layoutGraph(nodes, edges))
    const node = (name: string) => result.children!.find(node => node.id === 'component:' + name)!
    expect(node('sensor_1').y!).toBeLessThan(node('sensor_2').y!)
    expect(node('analyzer_1').y!).toBeLessThan(node('analyzer_2').y!)
    const inputs = node('alarm').ports!
    expect(inputs.find(port => port.id.endsWith(':alarm.machine_1'))!.y!).toBeLessThan(inputs.find(port => port.id.endsWith(':alarm.machine_2'))!.y!)
  })

  it('keeps inputs and outputs in displayed top-to-bottom order, including group nodes', async () => {
    const node = { id: 'group:pair', data: {
      inputs: ['first', 'second', 'third'].map(endpoint => ({ endpoint })),
      outputs: ['out1', 'out2', 'out3'].map(endpoint => ({ endpoint })),
    } }
    const result = await new ELK().layout(layoutGraph([node], []))
    const ports = result.children![0].ports!
    for (const endpoints of [['first', 'second', 'third'], ['out1', 'out2', 'out3']]) {
      const positions = endpoints.map(endpoint => ports.find(port => port.id === node.id + ':' + endpoint)!.y!)
      expect(positions[0]).toBeLessThan(positions[1])
      expect(positions[1]).toBeLessThan(positions[2])
    }
  })
})


it('packs expanded subsystems as nested containers with internal and crossing connections', async () => {
  const port = { root: 'Reading' }
  const leaf = (scope: string[]) => ({ inputs: { input: port }, outputs: { out: port }, _scope: scope })
  const model = { version: 2, components: {
    'left.source': leaf(['left']), 'left.sink': leaf(['left']), 'left.isolated': leaf(['left']),
    'left.inner.worker': leaf(['left', 'inner']),
    'right.source': leaf(['right']), 'right.sink': leaf(['right']),
  }, subscriptions: [
    { id: 'a', from: 'left.source:out', to: 'left.sink:input' },
    { id: 'b', from: 'left.sink:out', to: 'left.inner.worker:input' },
    { id: 'cross', from: 'left.inner.worker:out', to: 'right.source:input' },
    { id: 'c', from: 'right.source:out', to: 'right.sink:input' },
    { id: 'feedback', from: 'right.sink:out', to: 'left.source:input' },
  ], expandedDefinitions: [
    { name: 'left', scope: [] }, { name: 'left.inner', scope: ['left'] }, { name: 'right', scope: [] },
  ] }
  const graph = topology(model, {})
  const result = await new ELK().layout(layoutGraph(graph.nodes, graph.edges))
  expect(result.children!.map(node => node.id).sort()).toEqual(['definition:left', 'definition:right'])
  const left = result.children!.find(node => node.id === 'definition:left')!
  const right = result.children!.find(node => node.id === 'definition:right')!
  expect(left.x! + left.width! <= right.x! || right.x! + right.width! <= left.x! || left.y! + left.height! <= right.y! || right.y! + right.height! <= left.y!).toBe(true)
  function enclosed(parent: any) {
    for (const child of parent.children || []) {
      expect(child.x).toBeGreaterThanOrEqual(24)
      expect(child.y).toBeGreaterThanOrEqual(100)
      expect(child.x + child.width).toBeLessThanOrEqual(parent.width - 23)
      expect(child.y + child.height).toBeLessThanOrEqual(parent.height - 23)
      enclosed(child)
    }
  }
  result.children!.forEach(enclosed)
  const positions = layoutPositions(result)
  expect(Object.keys(positions)).toContain('left.inner.worker')
  expect(Object.keys(positions)).toContain('left.isolated')
  const redrawn = topology(model, { positions })
  for (const container of [left, right]) {
    const frame = redrawn.nodes.find(node => node.id === container.id)!
    expect(frame.position.x).toBeGreaterThanOrEqual(container.x! - 1)
    expect(frame.position.y).toBeGreaterThanOrEqual(container.y! - 1)
    expect(frame.position.x + parseFloat(frame.style.width)).toBeLessThanOrEqual(container.x! + container.width! + 1)
    expect(frame.position.y + parseFloat(frame.style.height)).toBeLessThanOrEqual(container.y! + container.height! + 1)
  }
})


it('reserves a visible footprint for an empty expanded definition', async () => {
  const model = { version: 2, components: {}, subscriptions: [], expandedDefinitions: [{ name: 'empty', scope: [] }] }
  const graph = topology(model, {})
  const result = await new ELK().layout(layoutGraph(graph.nodes, graph.edges))
  const frame = result.children![0]
  expect(frame.width).toBeGreaterThanOrEqual(344)
  expect(frame.height).toBeGreaterThanOrEqual(244)
  const drawn = topology(model, { positions: layoutPositions(result) }).nodes[0]
  expect(drawn.position.x).toBeCloseTo(frame.x!, 0)
  expect(drawn.position.y).toBeCloseTo(frame.y!, 0)
})

it('reflows resized definitions into rows without moving unrelated components', () => {
  const leaf = (scope: string[]) => ({ inputs: {}, outputs: {}, _scope: scope })
  const graph = topology({ version: 2, components: { 'group.a': leaf(['group']), 'group.b': leaf(['group']),
    'group.c': leaf(['group']), 'group.d': leaf(['group']), outside: leaf([]) },
    expandedDefinitions: [{ name: 'group', scope: [] }] }, {})
  for (const [width, height, rows] of [[1720, 268, 1], [872, 436, 2], [448, 772, 4]]) {
    const result = resizeLayout(graph.nodes, 'group', { x: 100, y: 200, width, height })
    expect(result.frameBounds.group).toEqual({ x: 100, y: 200, width, height })
    expect(result.positions.outside).toBeUndefined()
    const positions = ['a', 'b', 'c', 'd'].map(name => result.positions['group.' + name])
    expect(new Set(positions.map(position => position.y)).size).toBe(rows)
    for (const [i, position] of positions.entries()) {
      expect(position.x).toBeGreaterThanOrEqual(124)
      expect(position.y).toBeGreaterThanOrEqual(300)
      expect(position.x + 400).toBeLessThanOrEqual(100 + width - 24)
      expect(position.y + 144).toBeLessThanOrEqual(200 + height - 24)
      for (const other of positions.slice(i + 1)) {
        expect(position.x + 400 <= other.x || other.x + 400 <= position.x || position.y + 144 <= other.y || other.y + 144 <= position.y).toBe(true)
      }
    }
  }
})

it('moves nested frames as intact containers and preserves the opposite resize corner', () => {
  const graph = topology({ version: 2, components: {
    'parent.inner.worker': { inputs: {}, outputs: {}, _scope: ['parent', 'inner'] },
    'parent.other': { inputs: {}, outputs: {}, _scope: ['parent'] },
  }, expandedDefinitions: [{ name: 'parent', scope: [] }, { name: 'parent.inner', scope: ['parent'] }] }, {})
  const original = graph.nodes.find(node => node.id === 'definition:parent.inner')!
  const worker = graph.nodes.find(node => node.id === 'component:parent.inner.worker')!
  const requested = { x: 100, y: 200, width: 344, height: 244 }
  const result = resizeLayout(graph.nodes, 'parent', requested, 'nw')
  const parent = result.frameBounds.parent, inner = result.frameBounds['parent.inner']
  expect(parent.x + parent.width).toBe(requested.x + requested.width)
  expect(parent.y + parent.height).toBe(requested.y + requested.height)
  expect(inner.width).toBe(parseFloat(original.style.width))
  expect(inner.height).toBe(parseFloat(original.style.height))
  expect(result.positions['parent.inner.worker'].x - inner.x).toBe(worker.position.x - original.position.x)
  expect(result.positions['parent.inner.worker'].y - inner.y).toBe(worker.position.y - original.position.y)
  const redrawn = topology({ version: 2, components: {
    'parent.inner.worker': { inputs: {}, outputs: {}, _scope: ['parent', 'inner'] },
    'parent.other': { inputs: {}, outputs: {}, _scope: ['parent'] },
  }, expandedDefinitions: [{ name: 'parent', scope: [] }, { name: 'parent.inner', scope: ['parent'] }] },
  { positions: result.positions, views: { frame_bounds: result.frameBounds } })
  expect(redrawn.nodes.find(node => node.id === 'definition:parent')!.data.bounds).toEqual(parent)
})
