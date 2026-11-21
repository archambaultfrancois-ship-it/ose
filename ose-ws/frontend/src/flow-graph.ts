import { splitEndpoint } from './hierarchy'
export type Port = { root?: string; kind?: string; mode?: string; timeout?: number; timer?: { mode?: string; delay?: number; start?: string }; activates?: { message?: string[]; timeout?: string[] } }
export type Component = { inputs?: Record<string, Port>; outputs?: Record<string, Port> }
export type Subscription = { from: string; from_topic?: string; to: string; policy?: Record<string, any>; routing?: string }
export type Topology = { version: number; components: Record<string, Component>; external_topics?: Record<string, Port>; subscriptions: Record<string, Subscription>;
  composition?: { components: Record<string, { path: string[] }>; subscriptions?: Record<string, { path: string[] }>; instances: Record<string, { interface?: { inputs?: Record<string, string>; outputs?: Record<string, string> } }> } }
export type FlowNode = { id: string; name: string; kind: 'component' | 'external'; inputs?: Component['inputs']; outputs?: Component['outputs']; height: number; root?: string; x: number; y: number }
export type FlowEdge = Subscription & { id: string; source: string; target: string; root?: string; mode: 'push' | 'pull'; feedback: boolean; path: string; x: number; y: number }
export const nodeWidth = 224, nodeHeight = 140
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
export function layout(topology?: Topology | null, ports = false) {
  const nodes: FlowNode[] = [], edges: FlowEdge[] = []
  const byId = new Map<string, FlowNode>()
  function add(node: FlowNode) { nodes.push(node); byId.set(node.id, node) }
  for (const [name, desc] of Object.entries(topology?.components || {}).sort(([a], [b]) => a.localeCompare(b))) {
    add({ id: 'component:' + name, name, kind: 'component', inputs: desc.inputs, outputs: desc.outputs, height: ports ? Math.max(nodeHeight, 145 + 20 * Math.max(Object.keys(desc.inputs || {}).length, Object.keys(desc.outputs || {}).length)) : nodeHeight, x: 0, y: 0 })
  }
  for (const [name, desc] of Object.entries(topology?.external_topics || {}).sort(([a], [b]) => a.localeCompare(b))) {
    add({ id: 'external:' + name, name, kind: 'external', height: nodeHeight, root: desc.root, x: 0, y: 0 })
  }
  for (const [id, sub] of Object.entries(topology?.subscriptions || {}).sort(([a], [b]) => a.localeCompare(b))) {
    const [producer, output] = splitEndpoint(sub.from), consumer = splitEndpoint(sub.to)[0]
    // Older observation metadata has no external_topics or from_topic fields.
    const external = sub.from_topic !== undefined || byId.has('external:' + sub.from) || !byId.has('component:' + producer)
    const source = (external ? 'external:' + (sub.from_topic ?? sub.from) : 'component:' + producer)
    const target = 'component:' + consumer
    if (!byId.has(target)) continue
    if (!byId.has(source)) add({ id: source, name: sub.from_topic ?? sub.from, kind: 'external', height: nodeHeight, x: 0, y: 0 })
    const root = external ? byId.get(source)?.root : topology?.components[producer]?.outputs?.[output]?.root
    const input = splitEndpoint(sub.to)[1]
    const mode = byId.get(target)?.inputs?.[input]?.mode === 'pull' ? 'pull' : 'push'
    edges.push({ ...sub, id, source, target, root, mode, feedback: false, path: '', x: 0, y: 0 })
  }
  const outgoing = new Map(nodes.map(node => [node.id, [] as FlowEdge[]]))
  const incoming = new Map(nodes.map(node => [node.id, [] as FlowEdge[]]))
  for (const edge of edges) { outgoing.get(edge.source)!.push(edge); incoming.get(edge.target)!.push(edge) }
  // Push inputs structure the graph. A Pull link can place an otherwise
  // independent consumer, but must not reorder an established Push chain.
  // Keep a separate acyclic set of constraints: cycle-closing links remain
  // visible as feedback instead of blocking the entire topological traversal.
  const children = new Map(nodes.map(node => [node.id, new Set<string>()]))
  const parents = new Map(nodes.map(node => [node.id, new Set<string>()]))
  const pushConsumers = new Set(edges.filter(edge => edge.mode === 'push').map(edge => edge.target))
  for (const edge of [...edges.filter(edge => edge.mode === 'push'), ...edges.filter(edge => edge.mode === 'pull')]) {
    if (edge.mode === 'pull' && pushConsumers.has(edge.target)) continue
    const pending = [edge.target], visited = new Set<string>()
    while (pending.length) {
      const id = pending.pop()!
      if (visited.has(id)) continue
      visited.add(id)
      if (id === edge.source) break
      pending.push(...children.get(id)!)
    }
    if (visited.has(edge.source)) continue
    children.get(edge.source)!.add(edge.target)
    parents.get(edge.target)!.add(edge.source)
  }
  const degree = new Map(nodes.map(node => [node.id, parents.get(node.id)!.size]))
  const rank = new Map(nodes.map(node => [node.id, 0]))
  const ready = nodes.filter(node => !degree.get(node.id))
  for (let i = 0; i < ready.length; i++) {
    const source = ready[i].id
    for (const target of children.get(source)!) {
      rank.set(target, Math.max(rank.get(target)!, rank.get(source)! + 1))
      degree.set(target, degree.get(target)! - 1)
      if (!degree.get(target)) ready.push(byId.get(target)!)
    }
  }
  const columns = new Map<number, FlowNode[]>()
  for (const node of nodes) {
    const level = rank.get(node.id)!
    if (!columns.has(level)) columns.set(level, [])
    columns.get(level)!.push(node)
  }
  for (const [level, column] of [...columns].sort(([a], [b]) => a - b)) {
    // Preserve straight branch rows before placing shared consumers in the
    // nearest free space. This keeps parallel processing chains readable.
    const parentY = (node: FlowNode) => {
      const sources = [...parents.get(node.id)!].map(id => byId.get(id)!)
      return sources.length ? sources.reduce((sum, parent) => sum + parent.y + parent.height / 2, 0) / sources.length - node.height / 2 : margin
    }
    column.sort((a, b) => Number(parents.get(a.id)!.size > 1) - Number(parents.get(b.id)!.size > 1)
      || parentY(a) - parentY(b) || Number(pushConsumers.has(b.id)) - Number(pushConsumers.has(a.id)) || a.name.localeCompare(b.name))
    const placed: FlowNode[] = []
    for (const node of column) {
      const desired = Math.max(margin, parentY(node))
      const candidates = [desired, margin, ...placed.map(other => other.y + other.height + rowGap)]
      node.y = candidates.filter(y => placed.every(other => y + node.height + rowGap <= other.y || y >= other.y + other.height + rowGap))
        .sort((a, b) => Math.abs(a - desired) - Math.abs(b - desired) || a - b)[0]
      node.x = margin + level * (nodeWidth + columnGap)
      placed.push(node)
    }
  }
  let height = Math.max(nodeHeight + margin, ...nodes.map(node => node.y + node.height)) + margin
  const returns: { left: number; right: number; y: number }[] = []
  for (const edge of edges) {
    const source = byId.get(edge.source)!, target = byId.get(edge.target)!
    const from = outgoing.get(edge.source)!, to = incoming.get(edge.target)!
    const anchor = (node: FlowNode, output: boolean, qualified: string, siblings: FlowEdge[]) => {
      const names = Object.keys((output ? node.outputs : node.inputs) || {})
      const index = names.indexOf(splitEndpoint(qualified)[1])
      return node.y + (ports && index >= 0 ? portY(index) : node.height * (siblings.indexOf(edge) + 1) / (siblings.length + 1))
    }
    const x1 = source.x + nodeWidth, y1 = anchor(source, true, edge.from, from)
    const x2 = target.x, y2 = anchor(target, false, edge.to, to)
    edge.feedback = target.x <= source.x
    if (edge.feedback) {
      const left = x2 - 24, right = x1 + 24
      let y = Math.max(source.y + source.height, target.y + target.height) + rowGap / 2
      // Route returns through free horizontal corridors, with vertical legs
      // in column gutters, so feedback never runs through component cards.
      for (;;) {
        const blocked = nodes.filter(node => node.x < right && node.x + nodeWidth > left && y > node.y - 24 && y < node.y + node.height + 24)
        const occupied = returns.filter(route => route.left < right && route.right > left && Math.abs(route.y - y) < 48)
        if (!blocked.length && !occupied.length) break
        y = Math.max(y, ...blocked.map(node => node.y + node.height + rowGap / 2), ...occupied.map(route => route.y + 48))
      }
      returns.push({ left, right, y })
      edge.path = `M ${x1} ${y1} L ${right - 10} ${y1} Q ${right} ${y1} ${right} ${y1 + 10} L ${right} ${y - 10} Q ${right} ${y} ${right - 10} ${y} L ${left + 10} ${y} Q ${left} ${y} ${left} ${y - 10} L ${left} ${y2 + 10} Q ${left} ${y2} ${left + 10} ${y2} L ${x2} ${y2}`
      edge.x = (left + right) / 2; edge.y = y - 14
      height = Math.max(height, y + margin)
    } else {
      const bend = Math.max(48, Math.abs(x2 - x1) / 2)
      edge.path = `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
      edge.x = (x1 + x2) / 2; edge.y = (y1 + y2) / 2
    }
  }
  return { nodes, edges, width: margin * 2 + nodeWidth + Math.max(0, ...rank.values()) * (nodeWidth + columnGap), height }
}

export function portY(index: number) { return 145 + index * 20 }
