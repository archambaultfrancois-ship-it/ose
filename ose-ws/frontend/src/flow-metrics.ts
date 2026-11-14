// Diagnostics values keep the semantics and units of ose top.
export const metrics = {
  cpu: { label: 'Component CPU', unit: '%', value: (r: any) => r.resources?.component?.cpu_percent },
  memory: { label: 'Component RSS', unit: 'MiB', value: (r: any) => r.resources?.component?.rss_bytes == null ? null : r.resources.component.rss_bytes / 1048576 },
  timer: { label: 'Timer lag p95 (worst port)', unit: 'ms', value: (r: any) => maximum(r.timers) },
  publication: { label: 'Publication wait p95', unit: 'ms', value: (r: any) => r.publication_wait?.p95_ms },
  input: { label: 'Input wait p95 (worst port)', unit: 'ms', value: (r: any) => maximum(r.input_wait) },
  callback: { label: 'Callback p95', unit: 'ms', value: (r: any) => r.tick_p95_ms },
  in: { label: 'Admissions', unit: '/s', value: (r: any) => r.rates?.in },
  out: { label: 'Publications', unit: '/s', value: (r: any) => r.rates?.out },
}
export type Metric = keyof typeof metrics
function maximum(ports: any) {
  const values = Object.values(ports || {}).map((v: any) => v.p95_ms).filter(v => v != null)
  return values.length ? Math.max(...values) : null
}
export function available(row: any) { return row && !['UNAVAILABLE', 'STALE', 'STARTING'].includes(row.state) }
export function metricValue(row: any, metric: Metric): number | null {
  const value = available(row) ? metrics[metric].value(row) : null
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function linkHealth(edge: { id: string; from: string; to: string; source: string }, rows: Map<string, any>, error = false) {
  const source = rows.get(edge.from.split('.')[0]), target = rows.get(edge.to.split('.')[0])
  const delivery = !error && edge.source.startsWith('component:') && available(source)
    ? source.queues?.find((q: any) => q.scope === 'delivery' && q.name === edge.id) : undefined
  const input = !error && available(target)
    ? target.queues?.find((q: any) => q.scope === 'input' && q.name === edge.to.slice(edge.to.indexOf('.') + 1)) : undefined
  const recent = source?.rate_span_seconds > 0 ? delivery?.recent : undefined
  const rate = recent?.accepted == null ? null : recent.accepted / source.rate_span_seconds
  const refused = recent?.refused ?? null
  const lost = recent ? (recent.dropped_oldest || 0) + (recent.failed || 0) : null
  const full = delivery?.policy?.buffer === 'fifo' && delivery.policy.capacity > 0 && delivery.depth >= delivery.policy.capacity * .8
  return { delivery, input, rate, refused, lost, span: delivery ? source?.rate_span_seconds : null,
    severity: lost ? 'error' : refused || full ? 'warning' : recent ? 'ok' : 'unknown' }
}

export type HistoryPoint = { time: number; state: string; severity: string; publicationWaitMs?: number | null; gapBefore: boolean; values: Record<Metric, number | null> }
export function stateTransitions(points: HistoryPoint[]) {
  // Keep raw samples for metrics; only sustained publication waits enter the state log.
  const visible = points.filter(p => p.state !== 'WAIT PUB' || p.publicationWaitMs == null || p.publicationWaitMs >= 1000)
  return visible.filter((p, i) => !i || p.gapBefore || p.state !== visible[i - 1].state)
}
/** Browser-only history: at most 120 reports / 2 minutes per component. */
export class FlowHistory {
  epoch: unknown
  timestamp?: string
  interrupted = false
  rows = new Map<string, { generation: unknown; points: HistoryPoint[] }>()
  sample(report: any) {
    if (report?.epoch !== this.epoch) { this.rows.clear(); this.timestamp = undefined; this.epoch = report?.epoch }
    const time = Date.parse(report?.timestamp)
    if (!Number.isFinite(time) || report.timestamp === this.timestamp) return
    this.timestamp = report.timestamp
    const names = new Set((report.components || []).map((r: any) => r.name))
    for (const name of this.rows.keys()) if (!names.has(name)) this.rows.delete(name)
    for (const row of report.components || []) {
      const previous = this.rows.get(row.name)
      const points = previous && previous.generation === row.generation ? previous.points : []
      if (points.length && time <= points[points.length - 1].time) points.length = 0
      points.push({ time, state: row.state, severity: row.severity, publicationWaitMs: row.publication_wait?.current_ms, gapBefore: this.interrupted,
        values: Object.fromEntries((Object.keys(metrics) as Metric[]).map(key => [key, report.error ? null : metricValue(row, key)])) as HistoryPoint['values'] })
      this.rows.set(row.name, { generation: row.generation, points: points.filter(p => p.time >= time - 120000).slice(-120) })
    }
    this.interrupted = false
  }
}

export function sparkline(points: HistoryPoint[], metric: Metric) {
  const values = points.map(p => p.values[metric]).filter((v): v is number => v != null)
  const max = Math.max(1, ...values), end = points.at(-1)?.time || 0
  return points.map(point => ({ ...point, x: 16 + 192 * (point.time - end + 120000) / 120000,
    y: point.values[metric] == null ? null : 128 - 20 * point.values[metric]! / max }))
}
export function sparkPaths(points: ReturnType<typeof sparkline>) {
  const paths: string[] = []
  let path = '', previous = 0
  for (const point of points) {
    if (point.y == null || point.gapBefore || point.time - previous > 5000) { if (path) paths.push(path); path = '' }
    if (point.y != null) path += `${path ? ' L' : 'M'} ${point.x} ${point.y}`
    previous = point.time
  }
  if (path) paths.push(path)
  return paths
}
