import { expect, it } from 'vitest'
import { logicalName, scopedTopology, subsystemChildren, subsystemMembers } from '../src/hierarchy'
import { layout, type Topology } from '../src/flow-graph'

const topology: Topology = {
  version: 1,
  components: { sensor: { outputs: { out: { root: 'Value' } } }, ss_1: { inputs: { input: { root: 'Value' } }, outputs: { out: { root: 'Value' } } }, display: { inputs: { input: { root: 'Value' } } }, idle: {} },
  subscriptions: { input: { from: 'sensor.out', to: 'ss_1.input' }, output: { from: 'ss_1.out', to: 'display.input' } },
  composition: { components: { sensor: { path: ['acquisition', 'sensor'] }, ss_1: { path: ['monitoring', 'detection', 'processor'] }, display: { path: ['display'] } },
    instances: { acquisition: {}, monitoring: {}, 'monitoring.detection': {} } },
}
it('navigates instance paths while retaining portable runtime identities', () => {
  expect(subsystemChildren(topology, '')).toEqual(['acquisition', 'monitoring'])
  expect(subsystemChildren(topology, 'monitoring')).toEqual(['monitoring.detection'])
  expect(subsystemMembers(topology, 'monitoring')).toEqual(['ss_1'])
  expect(logicalName(topology, 'ss_1')).toBe('monitoring.detection.processor')
  expect(logicalName(topology, 'idle')).toBe('idle')
})
it('keeps boundary neighbors and both crossing links without mutating the topology', () => {
  const before = JSON.stringify(topology)
  const scoped = scopedTopology(topology, 'monitoring')!
  expect(Object.keys(scoped.components)).toEqual(['sensor', 'ss_1', 'display'])
  const graph = layout(scoped)
  expect(graph.edges.map(edge => edge.id)).toEqual(['input', 'output'])
  expect(graph.nodes.find(node => node.name === 'ss_1')?.id).toBe('component:ss_1')
  expect(JSON.stringify(topology)).toBe(before)
})
it('supports flat and older runtimes and discards unknown scopes', () => {
  const flat = { ...topology, composition: undefined }
  expect(scopedTopology(flat, 'missing')).toBe(flat)
  expect(subsystemChildren(flat, '')).toEqual([])
  expect(subsystemMembers(flat, '')).toHaveLength(4)
})
