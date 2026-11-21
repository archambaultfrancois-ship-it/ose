import { activationLinks, inputLabel } from './execution'
import { subsystemGraph } from './subsystems'
import { topology } from './graph'

export type TableRow = { name: string; detail: string; type: string; category: string; file?: string; path?: string[] }

/** Tables and the canvas share expansion, visual-group visibility and source ownership. */
export function tableRows(project: any, category = 'all'): TableRow[] {
  const model = subsystemGraph(project, project.workspace.views?.expanded_subsystems || [])
  if (!model || model.version !== 2) return []
  const graph = topology(model, project.workspace)
  const rows: TableRow[] = []
  const add = (row: TableRow) => { if (category === row.category || category === 'all' && ['components', 'subsystems', 'groups', 'external_topics', 'subscriptions'].includes(row.category)) rows.push(row) }
  function portSource(component: string, direction: string, port: string) {
    const source = model.components[component]
    if (source?._subsystem) return { file: source._subsystem.definition, path: ['interface', direction, port] }
    return { file: source?._model || project.model, path: ['components', source?._localName || component, direction, port] }
  }
  for (const node of graph.nodes) {
    const data = node.data, file = data.model || project.model, local = data.localName || data.name
    if (data.kind === 'definition' || data.subsystem) {
      add({ name: data.name, detail: data.kind === 'definition' ? 'Expanded · ' + data.file : data.definition?.definition || '',
        type: 'Subsystem', category: 'subsystems', file, path: ['subsystems', local] })
    } else if (data.kind === 'component') {
      add({ name: data.name, detail: file, type: activationLinks(model.components[data.name]).length + ' activation link(s)', category: 'components', file, path: ['components', local] })
    } else if (data.kind === 'group') {
      add({ name: data.name, detail: data.members.join(', '), type: 'Visual group', category: 'groups' })
    } else if (data.kind === 'external') {
      add({ name: data.name, detail: file, type: 'External topic', category: 'external_topics', file, path: ['external_topics', local] })
    }
    for (const direction of ['inputs', 'outputs']) {
      for (const port of data[direction]) {
        const origin = data.kind === 'external' ? { file, path: ['external_topics', local] } : portSource(port.component, direction, port.port)
        add({ name: port.endpoint, detail: direction === 'inputs' ? inputLabel(port.description) : direction,
          type: port.root || 'Timer · no message', category: 'ports', ...origin })
      }
    }
    if (data.kind === 'component') for (const link of activationLinks(model.components[data.name])) {
      add({ name: data.name + ':' + link.input + ' → ' + link.output, detail: link.event,
        type: 'Allowed output activation', category: 'activations', file, path: ['components', local, 'inputs', link.input] })
    }
  }
  for (const edge of graph.edges) {
    const sub = edge.data.sub
    add({ name: edge.id, detail: (sub.from || sub.from_topic) + ' → ' + sub.to,
      type: sub.qos?.buffer || 'fifo (default)', category: 'subscriptions', file: edge.data.model || project.model, path: ['subscriptions', String(edge.data.index)] })
  }
  const scopes = [{ file: project.model, name: '' }, ...(model.expandedDefinitions || []).map((definition: any) => ({
    file: project.subsystems[definition.model][definition.localName].model, name: definition.name,
  }))]
  for (const scope of scopes) {
    const source = project.documents[scope.file]?.value
    for (const [name, value] of Object.entries(source?.shared_parameters || {}) as any) {
      add({ name: scope.name ? scope.name + '/' + name : name, detail: value.schema || source.schemas,
        type: value.root, category: 'parameters', file: scope.file, path: ['shared_parameters', name] })
    }
    const deployments = scope.file === project.model ? project.deployments : project.model_contexts?.[scope.file]?.deployments || []
    for (const file of deployments) for (const [name, value] of Object.entries(project.documents[file]?.value?.components || {}) as any) {
      const qualified = scope.name ? scope.name + '.' + name : name
      if (!graph.nodes.some(node => node.data.kind === 'component' && node.data.name === qualified)) continue
      add({ name: qualified, detail: file, type: value.container ? value.container.language + ' · ' + value.container.handler : value.command?.join(' '),
        category: 'deployments', file, path: ['components', name] })
    }
  }
  return rows
}
