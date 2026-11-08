export function topology(model: any, workspace: any, labels = true) {
  const components = model?.components || {}, groups = workspace?.groups || {}, collapsed = new Map<string, string>()
  for (const [id, raw] of Object.entries(groups)) {
    const group = raw as any
    if (group.collapsed) for (const member of group.members || []) collapsed.set(member, 'group:' + id)
  }
  const nodes: any[] = [], endpoint = new Map<string, string>()
  for (const [name, raw] of Object.entries(components)) {
    const component = raw as any, id = collapsed.get(name) || 'component:' + name
    for (const direction of ['inputs', 'outputs']) for (const port of Object.keys(component[direction] || {})) endpoint.set(name + '.' + port, id)
    if (collapsed.has(name)) continue
    const ports = (direction: string) => Object.entries(component[direction] || {}).map(([port, type]: any) => ({ name: port, endpoint: name + '.' + port, root: type.root, required: type.required !== false }))
    nodes.push({ id, type: 'component', position: workspace?.positions?.[name] || { x: nodes.length % 4 * 340, y: Math.floor(nodes.length / 4) * 260 },
      data: { name, kind: 'component', trigger: component.trigger, inputs: ports('inputs'), outputs: ports('outputs') } })
  }
  for (const [id, raw] of Object.entries(groups)) {
    const group = raw as any
    if (!group.collapsed) continue
    const ports = (direction: string) => (group.members || []).flatMap((name: string) => Object.entries(components[name]?.[direction] || {}).map(([port, type]: any) => ({ name: name + '.' + port, endpoint: name + '.' + port, root: type.root, required: type.required !== false })))
    nodes.push({ id: 'group:' + id, type: 'component', position: workspace?.positions?.['group:' + id] || { x: 0, y: nodes.length * 200 }, data: { name: id, kind: 'group', inputs: ports('inputs'), outputs: ports('outputs') } })
  }
  for (const [name, type] of Object.entries(model?.external_topics || {}) as any) {
    const id = 'external:' + name
    nodes.push({ id, type: 'component', position: workspace?.positions?.[id] || { x: -340, y: nodes.length * 80 }, data: { name, kind: 'external', inputs: [], outputs: [{ name, endpoint: name, root: type.root }] } })
  }
  const edges = (model?.subscriptions || []).flatMap((sub: any, index: number) => {
    const source = sub.from ? endpoint.get(sub.from) : 'external:' + sub.from_topic, target = endpoint.get(sub.to)
    if (!source || !target || source === target && source.startsWith('group:')) return []
    return [{ id: sub.id, source, target, sourceHandle: sub.from || sub.from_topic, targetHandle: sub.to, label: labels ? sub.id : '', data: { index, sub }, type: 'smoothstep', markerEnd: 'arrowclosed' }]
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
