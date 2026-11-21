import type { Topology } from './flow-graph'

export function logicalName(topology: Topology | null | undefined, name: string): string {
  return topology?.composition?.components?.[name]?.path.join('.') || name
}

export function subsystemMembers(topology: Topology | null | undefined, scope: string): string[] {
  if (!scope) return Object.keys(topology?.components || {})
  const parts = scope.split('.')
  return Object.keys(topology?.components || {}).filter(name => {
    const path = topology?.composition?.components?.[name]?.path
    return path && path.length > parts.length && parts.every((part, index) => path[index] === part)
  })
}

export function subsystemChildren(topology: Topology | null | undefined, scope: string): string[] {
  const prefix = scope ? scope + '.' : ''
  return Object.keys(topology?.composition?.instances || {}).filter(path => path.startsWith(prefix) && !path.slice(prefix.length).includes('.')).sort()
}

/** Keep immediate neighbors so crossing links remain visible with their real IDs. */
export function scopedTopology(topology: Topology | null | undefined, scope: string): Topology | null | undefined {
  if (!topology || !scope || !topology.composition?.instances?.[scope]) return topology
  const members = new Set(subsystemMembers(topology, scope)), visible = new Set(members)
  const subscriptions = Object.fromEntries(Object.entries(topology.subscriptions).filter(([, sub]) => {
    const source = sub.from_topic ? null : splitEndpoint(sub.from)[0], target = splitEndpoint(sub.to)[0]
    if (!members.has(target) && (!source || !members.has(source))) return false
    if (source) visible.add(source)
    visible.add(target)
    return true
  }))
  const external = new Set(Object.values(subscriptions).map(sub => sub.from_topic).filter(Boolean))
  return { ...topology, components: Object.fromEntries(Object.entries(topology.components).filter(([name]) => visible.has(name))),
    external_topics: Object.fromEntries(Object.entries(topology.external_topics || {}).filter(([name]) => external.has(name))), subscriptions }
}

export function splitEndpoint(endpoint: string): [string, string] {
  const separator = endpoint.includes(':') ? ':' : '.'
  const index = endpoint.indexOf(separator)
  return index < 0 ? [endpoint, ''] : [endpoint.slice(0, index), endpoint.slice(index + 1)]
}
