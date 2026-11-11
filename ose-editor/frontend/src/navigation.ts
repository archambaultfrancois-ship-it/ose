import type { Selection } from './store'

export type NavigationTarget = Selection & { line?: number; column?: number }
export type NavigationItem = NavigationTarget & { id: string; label: string; kind: string; context: string; stale?: boolean }

export function navigationIndex(project: any): NavigationItem[] {
  const items: NavigationItem[] = []
  for (const [file, raw] of Object.entries(project?.documents || {})) {
    const doc = raw as any
    const add = (label: string, kind: string, path: string[] = [], extra: Partial<NavigationItem> = {}) => {
      items.push({ id: JSON.stringify([file, path, extra.sdl]), file, path, label, kind, context: file, stale: !!doc.stale, ...extra })
    }
    add(file, 'File')
    if (file === project.model && doc.value?.version === 2) {
      for (const [name, node] of Object.entries(doc.value.components || {}) as [string, any][]) {
        add(name, 'Component', ['components', name])
        for (const direction of ['inputs', 'outputs']) for (const [port, desc] of Object.entries(node?.[direction] || {}) as [string, any][]) {
          add(name + '.' + port, direction === 'outputs' ? 'Output' : desc?.kind === 'timer' ? 'Timer' : 'Input', ['components', name, direction, port], { context: `${file} · ${desc?.root || 'Timer'}` })
        }
      }
      for (const [index, sub] of (Array.isArray(doc.value.subscriptions) ? doc.value.subscriptions : []).entries()) if (sub && typeof sub === 'object') add(sub.id || String(index), 'Subscription', ['subscriptions', String(index)], { context: `${file} · ${sub.from || sub.from_topic} → ${sub.to}` })
      for (const name of Object.keys(doc.value.shared_parameters || {})) add(name, 'Parameter', ['shared_parameters', name])
      for (const name of Object.keys(doc.value.external_topics || {})) add(name, 'External topic', ['external_topics', name])
    } else if (Array.isArray(doc.nodes)) {
      const visit = (nodes: any[], parents: string[] = []) => {
        for (const node of nodes) {
          const names = [...parents, node.name].filter(Boolean)
          add(names.join('.'), node.kind || 'SDL declaration', [], { sdl: node.path })
          visit(node.children || [], names)
        }
      }
      visit(doc.nodes)
    } else if (doc.tree) {
      const visit = (node: any) => {
        if (node.path?.length) add(node.path.join('.'), doc.role === 'deployment' ? 'Deployment property' : 'Declaration', node.path)
        for (const child of node.children || []) visit(child)
      }
      visit(doc.tree)
    }
  }
  return items
}

function normalized(value: string) { return value.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase() }
export function searchNavigation(index: NavigationItem[], query: string): NavigationItem[] {
  const terms = normalized(query).trim().split(/\s+/).filter(Boolean)
  return index.filter(item => terms.every(term => normalized(`${item.label} ${item.kind} ${item.context}`).includes(term)))
    .map((item, order) => ({ item, order, score: terms.length && normalized(item.label) === terms.join(' ') ? 0 : terms.length && normalized(item.label).startsWith(terms.join(' ')) ? 1 : 2 }))
    .sort((a, b) => a.score - b.score || a.order - b.order).map(entry => entry.item)
}
