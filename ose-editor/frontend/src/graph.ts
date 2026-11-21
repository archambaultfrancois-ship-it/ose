import { receptionFor } from './reception'
import { activationLinks, inputKind, inputMode, inputLabel } from './execution'

const portEndpoint = (name: string, port: string) => name + (name.includes('.') ? ':' : '.') + port

export type LinkStyle = 'default' | 'straight' | 'smoothstep'

export function nodeSize(node: any) {
  if (node.data.kind === 'definition') return { width: parseFloat(node.style.width), height: parseFloat(node.style.height) }
  const rows = Math.max(node.data.inputs.length, node.data.outputs.length, 1)
  return { width: node.data.kind === 'component' ? 400 : 270,
    height: node.data.kind === 'component' ? 80 + rows * 64 : node.data.subsystem ? 64 + rows * 34
      : 80 + (node.data.inputs.length + node.data.outputs.length) * 34 }
}

export function topology(model: any, workspace: any, labels = true, linkStyle: LinkStyle = 'default', showActivations = false) {
  if (model?.version !== 2) return { nodes: [], edges: [] }
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
  const portData = (name: string, direction: string, port: string, desc: any, label = port) => ({
    name: label, port, component: name, endpoint: portEndpoint(name, port), root: desc.root,
    publicAliases: desc.publicAliases || [], kind: inputKind(desc), mode: inputMode(desc), description: desc,
    label: inputLabel(desc), connectable: direction !== 'inputs' || inputKind(desc) !== 'timer',
    ...(direction === 'inputs' && inputKind(desc) !== 'timer' ? { reception: receptionFor(model, portEndpoint(name, port)) } : {}),
  })
  for (const [name, raw] of Object.entries(components)) {
    const component = raw as any, kind = component._subsystem ? 'group' : 'component', id = collapsed.get(name) || (component._subsystem ? 'subsystem' : kind) + ':' + name
    for (const direction of ['inputs', 'outputs']) for (const port of Object.keys(component[direction] || {})) endpoint.set(portEndpoint(name, port), id)
    if (collapsed.has(name)) continue
    const ports = (direction: string) => Object.entries(component[direction] || {}).map(([port, desc]) => portData(name, direction, port, desc))
    nodes.push({ id, type: 'component', position: workspace?.positions?.[name] || { x: nodes.length % 4 * 440, y: Math.floor(nodes.length / 4) * 400 },
      data: { name, kind, subsystem: !!component._subsystem, definition: component._subsystem, scope: component._scope || [], localName: component._localName || name, model: component._model, showActivations,
        activations: kind === 'component' ? activationLinks(component) : [], inputs: ports('inputs'), outputs: ports('outputs') } })
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
        .filter(([port]) => !internal.has(portEndpoint(name, port)) || crossing.has(portEndpoint(name, port)))
        .map(([port, desc]) => portData(name, direction, port, desc, portEndpoint(name, port))))
    }
    nodes.push({ id: 'group:' + id, type: 'component', position: workspace?.positions?.['group:' + id] || { x: 0, y: nodes.length * 200 }, data: { name: id, kind: 'group', members, inputs: ports('inputs'), outputs: ports('outputs') } })
  }
  for (const [name, type] of Object.entries(model?.external_topics || {}) as any) {
    const id = 'external:' + name
    nodes.push({ id, type: 'component', position: workspace?.positions?.[id] || { x: -340, y: nodes.length * 80 }, data: { name, kind: 'external', model: type._model, localName: type._localName || name, inputs: [], outputs: [{ name, endpoint: name, root: type.root }] } })
  }
  const edges = (model?.subscriptions || []).flatMap((sub: any, index: number) => {
    const source = sub.from ? endpoint.get(sub.from) : 'external:' + sub.from_topic, target = endpoint.get(sub.to)
    if (!source || !target || source === target && source.startsWith('group:')) return []
    return [{ id: sub.id, source, target, sourceHandle: sub.from || sub.from_topic, targetHandle: sub.to, label: labels ? sub.id : '', data: { index: sub._index ?? index, model: sub._model, sub }, type: linkStyle, markerEnd: 'arrowclosed' }]
  })
  // Keep the definition visible around its expanded graph, including isolated leaves.
  const frames: any[] = []
  const definitions = [...(model.expandedDefinitions || [])].sort((a, b) => b.name.split('.').length - a.name.split('.').length)
  for (const definition of definitions) {
    const members = [...nodes, ...frames].filter(node => node.data.name.startsWith(definition.name + '.'))
    const padding = 24, heading = 100
    const anchor = workspace?.positions?.[definition.name] || { x: 0, y: 0 }
    const left = Math.min(...members.map(node => node.position.x), members.length ? Infinity : anchor.x) - padding
    const top = Math.min(...members.map(node => node.position.y), members.length ? Infinity : anchor.y) - heading
    const right = Math.max(...members.map(node => node.position.x + nodeSize(node).width), left + 320)
    const bottom = Math.max(...members.map(node => node.position.y + nodeSize(node).height), top + 220)
    const minimum = { x: left, y: top, width: right - left + padding, height: bottom - top + padding }
    const saved = workspace?.views?.frame_bounds?.[definition.name]
    const bounds = saved ? {
      x: Math.min(saved.x, minimum.x), y: Math.min(saved.y, minimum.y),
      width: Math.max(saved.x + saved.width, minimum.x + minimum.width) - Math.min(saved.x, minimum.x),
      height: Math.max(saved.y + saved.height, minimum.y + minimum.height) - Math.min(saved.y, minimum.y),
    } : minimum
    frames.push({ id: 'definition:' + definition.name, type: 'subsystem-frame', position: { x: bounds.x, y: bounds.y },
      style: { width: bounds.width + 'px', height: bounds.height + 'px' },
      selectable: false, draggable: true, connectable: false, focusable: false, zIndex: -1000 + definition.name.split('.').length,
      data: { ...definition, bounds, minimumBounds: minimum, kind: 'definition', inputs: [], outputs: [], members: members.map(node => node.data.name) } })
  }
  return { nodes: [...frames, ...nodes], edges }
}

export function downstream(model: any, selected: string, direction = 'both'): Set<string> {
  if (direction === 'both') return new Set([...downstream(model, selected, 'upstream'), ...downstream(model, selected, 'downstream')])
  const found = new Set([selected]); let changed = true
  while (changed) {
    changed = false
    for (const sub of model?.subscriptions || []) {
      if (!sub.from) continue
      const a = sub.from.split(sub.from.includes(':') ? ':' : '.')[0], b = sub.to.split(sub.to.includes(':') ? ':' : '.')[0]
      if ((direction !== 'upstream' && found.has(a) || direction !== 'downstream' && found.has(b)) && (!found.has(a) || !found.has(b))) { found.add(a); found.add(b); changed = true }
    }
  }
  return found
}
