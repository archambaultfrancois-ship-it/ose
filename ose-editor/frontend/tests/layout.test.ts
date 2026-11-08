import { describe, expect, it } from 'vitest'
import ELK from 'elkjs/lib/elk.bundled.js'
import { topology } from '../src/graph'
import { layoutGraph } from '../src/layout'

describe('auto layout port order', () => {
  it('places branch 1 above branch 2 to match the visible alarm input order', async () => {
    const port = { root: 'Reading' }
    const model = { components: {
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
