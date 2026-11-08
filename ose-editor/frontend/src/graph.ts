import { receptionFor } from './reception'

export type LinkStyle = 'default' | 'straight' | 'smoothstep'

export function topology(model: any, workspace: any, labels = true, linkStyle: LinkStyle = 'default') {
  const components = model?.components || {}, groups = workspace?.groups || {}, collapsed = new Map<string, string>()
  const visibleGroups = new Map<string, string[]>()
  const membersOf = (id: string, ancestors = new Set<string>()): string[] => {
    if (!groups[id] || ancestors.has(id)) return []
    const path = new Set(ancestors).add(id), group = groups[id]
    return [...new Set<string>([
      ...(group.members || []).filter((name: string) => components[name]),
      ...(group.groups || []).flatMap((child: string) => membersOf(child, path)),
    ])]
  }
  const children = new Set<string>(Object.values(groups).flatMap((group: any) => group.groups || []))
  const visit = (id: string, ancestors = new Set<string>()) => {
    if (!groups[id] || ancestors.has(id)) return
    if (groups[id].collapsed) {
      const members = membersOf(id)
      if (members.length) {
        visibleGroups.set(id, members)
        for (const member of members) collapsed.set(member, 'group:' + id)
      }
    } else for (const child of groups[id].groups || []) visit(child, new Set(ancestors).add(id))
  }
  for (const id of Object.keys(groups)) if (!children.has(id)) visit(id)
  const nodes: any[] = [], endpoint = new Map<string, string>()
  for (const [name, raw] of Object.entries(components)) {
    const component = raw as any, id = collapsed.get(name) || 'component:' + name
    for (const direction of ['inputs', 'outputs']) for (const port of Object.keys(component[direction] || {})) endpoint.set(name + '.' + port, id)
    if (collapsed.has(name)) continue
    const ports = (direction: string) => Object.entries(component[direction] || {}).map(([port, type]: any) => ({ name: port, endpoint: name + '.' + port, root: type.root, required: type.required !== false, ...(direction === 'inputs' ? { reception: receptionFor(model, name + '.' + port) } : {}) }))
    nodes.push({ id, type: 'component', position: workspace?.positions?.[name] || { x: nodes.length % 4 * 340, y: Math.floor(nodes.length / 4) * 260 },
      data: { name, kind: 'component', trigger: component.trigger, inputs: ports('inputs'), outputs: ports('outputs') } })
  }
  // A collapsed group exposes crossing connections and free ports, while
  // ports used exclusively by hidden internal links stay inside the group.
  const internalInputs = new Set<string>(), internalOutputs = new Set<string>()
  const crossingInputs = new Set<string>(), crossingOutputs = new Set<string>()
  for (const sub of model?.subscriptions || []) {
    const source = sub.from ? endpoint.get(sub.from) : 'external:' + sub.from_topic, target = endpoint.get(sub.to)
    if (!source || !target) continue
    const internal = source === target && source.startsWith('group:')
    const inputs = internal ? internalInputs : crossingInputs
    inputs.add(sub.to)
    if (sub.from) (internal ? internalOutputs : crossingOutputs).add(sub.from)
  }
  for (const [id, members] of visibleGroups) {
    const ports = (direction: string) => {
      const internal = direction === 'inputs' ? internalInputs : internalOutputs, crossing = direction === 'inputs' ? crossingInputs : crossingOutputs
      return members.flatMap((name: string) => Object.entries(components[name]?.[direction] || {})
        .filter(([port]) => !internal.has(name + '.' + port) || crossing.has(name + '.' + port))
        .map(([port, type]: any) => ({ name: name + '.' + port, endpoint: name + '.' + port, root: type.root, required: type.required !== false, ...(direction === 'inputs' ? { reception: receptionFor(model, name + '.' + port) } : {}) })))
    }
    nodes.push({ id: 'group:' + id, type: 'component', position: workspace?.positions?.['group:' + id] || { x: 0, y: nodes.length * 200 }, data: { name: id, kind: 'group', inputs: ports('inputs'), outputs: ports('outputs') } })
  }
  for (const [name, type] of Object.entries(model?.external_topics || {}) as any) {
    const id = 'external:' + name
    nodes.push({ id, type: 'component', position: workspace?.positions?.[id] || { x: -340, y: nodes.length * 80 }, data: { name, kind: 'external', inputs: [], outputs: [{ name, endpoint: name, root: type.root }] } })
  }
  const edges = (model?.subscriptions || []).flatMap((sub: any, index: number) => {
    const source = sub.from ? endpoint.get(sub.from) : 'external:' + sub.from_topic, target = endpoint.get(sub.to)
    if (!source || !target || source === target && source.startsWith('group:')) return []
    return [{ id: sub.id, source, target, sourceHandle: sub.from || sub.from_topic, targetHandle: sub.to, label: labels ? sub.id : '', data: { index, sub }, type: linkStyle, markerEnd: 'arrowclosed' }]
  })
  return { nodes, edges }
}

export function downstream(model: any, selected: string, direction = 'both'): Set<string> {
  if (direction === 'both') return new Set([...downstream(model, selected, 'upstream'), ...downstream(model, selected, 'downstream')])
  const found = new Set([selected]); let changed = true
  while (changed) {
    changed = false
    for (const sub of model?.subscriptions || []) {
      if (!sub.from) continue
      const a = sub.from.split('.')[0], b = sub.to.split('.')[0]
      if ((direction !== 'upstream' && found.has(a) || direction !== 'downstream' && found.has(b)) && (!found.has(a) || !found.has(b))) { found.add(a); found.add(b); changed = true }
    }
  }
  return found
}
