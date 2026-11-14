import { expect, it } from 'vitest'
import { FlowHistory, linkHealth, metricValue, sparkline, sparkPaths, stateTransitions } from '../src/flow-metrics'

it('uses component resources, worst-port p95 and preserves unavailable values', () => {
  const row = { state: 'ACTIVE', resources: { runtime: { cpu_percent: 80 }, component: { cpu_percent: 12, rss_bytes: 2097152 } },
    timers: { a: { p95_ms: 3 }, b: { p95_ms: 7 }, c: { p95_ms: null } }, input_wait: {}, publication_wait: { p95_ms: 4 } }
  expect(metricValue(row, 'cpu')).toBe(12)
  expect(metricValue(row, 'memory')).toBe(2)
  expect(metricValue(row, 'timer')).toBe(7)
  expect(metricValue(row, 'input')).toBeNull()
  expect(metricValue(row, 'publication')).toBe(4)
  expect(metricValue({ ...row, state: 'STALE' }, 'cpu')).toBeNull()
  expect(metricValue(undefined, 'cpu')).toBeNull()
})

it('keeps subscription delivery metrics separate from shared destination queues and other fanout links', () => {
  const delivery = { name: 'one', scope: 'delivery', depth: 4, policy: { buffer: 'fifo', capacity: 4 }, recent: { accepted: 20, refused: 3, failed: 1, dropped_oldest: 2 } }
  const input = { name: 'value', scope: 'input', depth: 8 }
  const rows = new Map([
    ['a', { state: 'ACTIVE', rate_span_seconds: 5, queues: [delivery, { ...delivery, name: 'two', recent: { accepted: 100 } }] }],
    ['b', { state: 'ACTIVE', queues: [input] }],
  ])
  const edge = { id: 'one', from: 'a.out', to: 'b.value', source: 'component:a' }
  expect(linkHealth(edge, rows)).toMatchObject({ delivery, input, rate: 4, refused: 3, lost: 3, severity: 'error' })
  expect(linkHealth({ ...edge, id: 'missing' }, rows)).toMatchObject({ delivery: undefined, input, rate: null, severity: 'unknown' })
  expect(linkHealth({ ...edge, source: 'external:a.out' }, rows).delivery).toBeUndefined()
  expect(linkHealth(edge, rows, true)).toMatchObject({ delivery: undefined, input: undefined, severity: 'unknown' })
  rows.get('a')!.rate_span_seconds = 0
  expect(linkHealth(edge, rows)).toMatchObject({ rate: null, refused: null, lost: null, severity: 'warning' })
  rows.get('a')!.state = 'STALE'
  expect(linkHealth(edge, rows)).toMatchObject({ delivery: undefined, input, severity: 'unknown' })
})

const base = Date.parse('2026-11-14T10:00:00Z')
it('omits brief publication waits from transitions while preserving metrics, prolonged waits and errors', () => {
  const history = new FlowHistory()
  const states = ['ACTIVE', 'WAIT PUB', 'ACTIVE', 'WAIT PUB', 'WAIT PUB', 'LOSS/ERROR', 'ACTIVE']
  states.forEach((state, second) => {
    const sample: any = report(second, 'g1', 'e1', state)
    sample.components[0].publication_wait = { current_ms: [null, 2, null, 999, 1000, null, null][second] }
    history.sample(sample)
  })
  const points = history.rows.get('a')!.points
  expect(stateTransitions(points).map(p => [p.state, p.time - base])).toEqual([
    ['ACTIVE', 0], ['WAIT PUB', 4000], ['LOSS/ERROR', 5000], ['ACTIVE', 6000],
  ])
  expect(points).toHaveLength(7)
  expect(points[1].values.out).toBe(1)
})
function report(second: number, generation = 'g1', epoch = 'e1', state = 'ACTIVE', value = second) {
  return { timestamp: new Date(base + second * 1000).toISOString(), epoch,
    components: [{ name: 'a', generation, state, severity: 'ok', rates: { out: value } }] }
}

it('bounds histories, deduplicates reports and resets on component generation and application epoch', () => {
  const history = new FlowHistory()
  for (let i = 0; i < 200; i++) history.sample(report(i))
  expect(history.rows.get('a')!.points).toHaveLength(120)
  history.sample(report(199))
  expect(history.rows.get('a')!.points).toHaveLength(120)
  history.sample(report(200, 'g2'))
  expect(history.rows.get('a')!.points).toHaveLength(1)
  history.sample(report(201, 'g2', 'e2'))
  expect(history.rows.get('a')!.points).toHaveLength(1)
  history.sample(report(400, 'g2', 'e2'))
  expect(history.rows.get('a')!.points).toHaveLength(1)
  history.sample({ ...report(401), components: [] })
  expect(history.rows.size).toBe(0)
})

it('breaks sparklines across unavailable samples and long gaps, preserving state transitions and spikes', () => {
  const history = new FlowHistory()
  for (const [second, state, value] of [[0, 'IDLE', 0], [1, 'ACTIVE', 80], [2, 'STALE', 900], [3, 'DISABLED', 0], [20, 'ACTIVE', 2]] as const)
    history.sample(report(second, 'g1', 'e1', state, value))
  const points = history.rows.get('a')!.points
  expect(points.map(p => p.state)).toEqual(['IDLE', 'ACTIVE', 'STALE', 'DISABLED', 'ACTIVE'])
  expect(points[2].values.out).toBeNull()
  const spark = sparkline(points, 'out')
  expect(spark[1].y).toBe(108)
  expect(spark[0].y).toBe(128)
  expect(sparkPaths(spark)).toHaveLength(3)
  expect(sparkPaths(spark).join()).not.toMatch(/NaN|undefined/)
  history.sample({ ...report(21), error: 'disconnected' })
  expect(history.rows.get('a')!.points.at(-1)!.values.out).toBeNull()
  history.sample(report(22))
  history.interrupted = true
  history.sample(report(23))
  expect(history.rows.get('a')!.points.at(-1)!.gapBefore).toBe(true)
  expect(sparkPaths(sparkline(history.rows.get('a')!.points.slice(-2), 'out'))).toHaveLength(2)
})
