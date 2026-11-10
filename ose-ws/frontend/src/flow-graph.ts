export type Port = { root?: string; kind?: string; mode?: string; timeout?: number; timer?: { mode?: string; delay?: number; start?: string }; activates?: { message?: string[]; timeout?: string[] } }
export type Component = { inputs?: Record<string, Port>; outputs?: Record<string, Port> }
export type Subscription = { from: string; from_topic?: string; to: string; policy?: Record<string, any>; routing?: string }
export type Topology = { version: number; components: Record<string, Component>; external_topics?: Record<string, Port>; subscriptions: Record<string, Subscription> }
export type FlowNode = { id: string; name: string; kind: 'component' | 'external'; inputs?: Component['inputs']; root?: string; x: number; y: number }
export type FlowEdge = Subscription & { id: string; source: string; target: string; root?: string; path: string; x: number; y: number }
export const nodeWidth = 224, nodeHeight = 100
const margin = 32, columnGap = 156, rowGap = 64

export function inputSummary(inputs?: Component['inputs']) {
  if (!inputs) return 'Inputs unavailable'
  const values = Object.values(inputs)
  const timers = values.filter(port => port.kind === 'timer').length
  const pull = values.filter(port => port.kind !== 'timer' && port.mode === 'pull').length
  const push = values.length - timers - pull
  return [timers ? `${timers} timer${timers === 1 ? '' : 's'}` : '', push ? `${push} Push` : '', pull ? `${pull} Pull` : ''].filter(Boolean).join(' · ') || 'No activating inputs'
}

export function inputDetails(inputs?: Component['inputs']) {
  return Object.entries(inputs || {}).map(([name, port]) => {
    const description = port.kind === 'timer'
      ? `${port.timer?.mode === 'one_shot' ? 'One-shot' : 'Periodic'} timer · ${port.timer?.delay ?? '?'} s · ${port.timer?.start || 'automatic'}`
      : `${port.mode === 'pull' ? 'Pull' : 'Push'}${port.timeout != null ? ` · inactivity ${port.timeout} s` : ''}`
    const permissions = Object.entries(port.activates || {}).map(([event, outputs]) => `${event} → ${outputs.join(', ') || 'no outputs'}`)
    return `${name}: ${description}${permissions.length ? ' · ' + permissions.join(' · ') : ''}`
  }).join('\n')
}

/** Stable layout of data links, including isolated components and feedback. */
export function layout(topology?: Topology | null) {
  const nodes: FlowNode[] = [], edges: FlowEdge[] = []
  const byId = new Map<string, FlowNode>()
  function add(node: FlowNode) { nodes.push(node); byId.set(node.id, node) }
  for (const [name, desc] of Object.entries(topology?.components || {}).sort(([a], [b]) => a.localeCompare(b))) {
    add({ id: 'component:' + name, name, kind: 'component', inputs: desc.inputs, x: 0, y: 0 })
  }
  for (const [name, desc] of Object.entries(topology?.external_topics || {}).sort(([a], [b]) => a.localeCompare(b))) {
    add({ id: 'external:' + name, name, kind: 'external', root: desc.root, x: 0, y: 0 })
  }
  for (const [id, sub] of Object.entries(topology?.subscriptions || {}).sort(([a], [b]) => a.localeCompare(b))) {
    const [producer, output] = sub.from.split('.'), consumer = sub.to.split('.')[0]
    // Older observation metadata has no external_topics or from_topic fields.
    const external = sub.from_topic !== undefined || byId.has('external:' + sub.from) || !byId.has('component:' + producer)
    const source = (external ? 'external:' + (sub.from_topic ?? sub.from) : 'component:' + producer)
    const target = 'component:' + consumer
    if (!byId.has(target)) continue
    if (!byId.has(source)) add({ id: source, name: sub.from_topic ?? sub.from, kind: 'external', x: 0, y: 0 })
    const root = external ? byId.get(source)?.root : topology?.components[producer]?.outputs?.[output]?.root
    edges.push({ ...sub, id, source, target, root, path: '', x: 0, y: 0 })
  }
  const outgoing = new Map(nodes.map(node => [node.id, [] as FlowEdge[]]))
  const incoming = new Map(nodes.map(node => [node.id, [] as FlowEdge[]]))
  for (const edge of edges) { outgoing.get(edge.source)!.push(edge); incoming.get(edge.target)!.push(edge) }
  const degree = new Map(nodes.map(node => [node.id, incoming.get(node.id)!.length]))
  const rank = new Map(nodes.map(node => [node.id, 0]))
  const ready = nodes.filter(node => !degree.get(node.id))
  for (let i = 0; i < ready.length; i++) {
    for (const edge of outgoing.get(ready[i].id)!) {
      rank.set(edge.target, Math.max(rank.get(edge.target)!, rank.get(edge.source)! + 1))
      degree.set(edge.target, degree.get(edge.target)! - 1)
      if (!degree.get(edge.target)) ready.push(byId.get(edge.target)!)
    }
  }
  // Data feedback is valid when the activation graph is acyclic. Keep cycle
  // members in finite columns without requiring a component-level DAG.
  const columns = new Map<number, FlowNode[]>()
  for (const node of nodes) {
    const level = rank.get(node.id)!
    if (!columns.has(level)) columns.set(level, [])
    columns.get(level)!.push(node)
  }
  const height = Math.max(1, ...[...columns.values()].map(column => column.length)) * (nodeHeight + rowGap) - rowGap + margin * 2
  for (const [level, column] of [...columns].sort(([a], [b]) => a - b)) {
    // Keep branches near their parents without moving nodes when metrics refresh.
    const parentY = (node: FlowNode) => {
      const parents = incoming.get(node.id)!.map(edge => byId.get(edge.source)!).filter(parent => rank.get(parent.id)! < level)
      return parents.length ? parents.reduce((sum, parent) => sum + parent.y, 0) / parents.length : 0
    }
    column.sort((a, b) => parentY(a) - parentY(b) || a.name.localeCompare(b.name))
    column.forEach((node, index) => {
      node.x = margin + level * (nodeWidth + columnGap)
      node.y = (height - (column.length * (nodeHeight + rowGap) - rowGap)) / 2 + index * (nodeHeight + rowGap)
    })
  }
  for (const edge of edges) {
    const source = byId.get(edge.source)!, target = byId.get(edge.target)!
    const from = outgoing.get(edge.source)!, to = incoming.get(edge.target)!
    const x1 = source.x + nodeWidth, y1 = source.y + nodeHeight * (from.indexOf(edge) + 1) / (from.length + 1)
    const x2 = target.x, y2 = target.y + nodeHeight * (to.indexOf(edge) + 1) / (to.length + 1)
    const bend = Math.max(48, Math.abs(x2 - x1) / 2)
    edge.path = `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
    edge.x = (x1 + x2) / 2; edge.y = (y1 + y2) / 2
  }
  return { nodes, edges, width: margin * 2 + nodeWidth + Math.max(0, ...rank.values()) * (nodeWidth + columnGap), height }
}
