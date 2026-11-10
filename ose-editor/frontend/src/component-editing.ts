import type { ActivationLink } from './execution'

export function selectActivation(store: any, name: string, link: ActivationLink) {
  const node = store.project.documents[store.project.model].value.components[name]
  const index = node.inputs[link.input].activates[link.event].indexOf(link.output)
  store.select(store.project.model, ['components', name, 'inputs', link.input, 'activates', link.event, String(index)])
}

export function selectedActivation(store: any): ActivationLink | null {
  const path = store.selection?.path || []
  if (store.selection?.file !== store.project?.model || path[0] !== 'components' || path[1] !== store.editingComponent || path[2] !== 'inputs' || path[4] !== 'activates') return null
  const node = store.project.documents[store.project.model].value.components[path[1]]
  const output = node?.inputs[path[3]]?.activates?.[path[5]]?.[Number(path[6])]
  return typeof output === 'string' ? { input: path[3], event: path[5], output } : null
}

export function rootTypes(project: any): string[] {
  const roots = new Set<string>()
  for (const doc of Object.values(project?.documents || {}) as any[]) {
    for (const node of doc.nodes || []) if (node.kind === 'message') roots.add(node.name)
    for (const component of Object.values(doc.value?.components || {}) as any[]) {
      for (const desc of [...Object.values(component.inputs || {}), ...Object.values(component.outputs || {})] as any[])
        if (desc.root) roots.add(desc.root)
    }
  }
  return [...roots].sort()
}
