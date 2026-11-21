/** A graph projection of public interfaces; source model documents stay unchanged. */
export function graphModel(project: any): any {
  const model = project?.documents?.[project.model]?.value
  if (!model) return model
  const schemas = project.schema_contexts?.[project.model] || {}
  const component = (raw: any) => ({ ...raw, ...Object.fromEntries(['inputs', 'outputs'].map(direction => [direction,
    Object.fromEntries(Object.entries(raw[direction] || {}).map(([name, value]: any) => [name,
      { ...value, ...(value.kind !== 'timer' ? { schema: schemas[value.schema || model.schemas] || value.schema || model.schemas } : {}) }]))])) })
  const components = Object.fromEntries(Object.entries(model.components || {}).map(([name, raw]) => [name, component(raw)]))
  for (const name of Object.keys(model.subsystems || {})) {
    if (components[name]) continue
    const definition = project.subsystems?.[project.model]?.[name]
    components[name] = { inputs: definition?.inputs || {}, outputs: definition?.outputs || {}, _subsystem: definition || { unresolved: true } }
  }
  return { ...model, components, schemas: schemas[model.schemas] || model.schemas }
}

/** Expand authored definitions for display, preserving the document owning each edit. */
export function subsystemGraph(project: any, expanded: string[] = []): any {
  if (!project?.documents?.[project.model]?.value) return graphModel(project)
  const components: Record<string, any> = {}, subscriptions: any[] = [], external: Record<string, any> = {}
  const opened = new Set(expanded)
  const interfaces: { file: string; scope: string[]; name: string; entry: any }[] = []
  const qualify = (scope: string[], name: string) => [...scope, name].join('.')
  const endpoint = (name: string, port: string) => name + (name.includes('.') ? ':' : '.') + port
  function resolvePort(file: string, scope: string[], value: string, direction: string, seen = new Set<string>()): string {
    const separator = value.includes(':') ? ':' : '.'
    const index = value.indexOf(separator), local = value.slice(0, index), port = value.slice(index + 1)
    const name = qualify(scope, local), entry = project.subsystems?.[file]?.[local]
    if (entry && opened.has(name) && !seen.has(entry.definition)) {
      const target = project.documents[entry.definition]?.value?.interface?.[direction]?.[port]
      if (target) return resolvePort(entry.model, [...scope, local], target[direction === 'inputs' ? 'to' : 'from'], direction, new Set(seen).add(entry.definition))
    }
    return endpoint(name, port)
  }
  function visit(file: string, scope: string[], seen: Set<string>) {
    const model = graphModel({ ...project, model: file })
    if (!model) return
    for (const [local, raw] of Object.entries(model.components || {}) as any) {
      const name = qualify(scope, local), entry = raw._subsystem
      if (entry && opened.has(name) && !entry.unresolved && !seen.has(entry.definition)) {
        interfaces.push({ file, scope, name, entry })
        visit(entry.model, [...scope, local], new Set(seen).add(entry.definition))
      } else {
        components[name] = { ...raw, _scope: scope, _localName: local, _model: file }
      }
    }
    for (const [name, desc] of Object.entries(model.external_topics || {}) as [string, any][]) external[scope.length ? scope.join('.') + '/' + name : name] = { ...desc, _model: file, _localName: name }
    for (const [index, sub] of (model.subscriptions || []).entries()) {
      subscriptions.push({ ...sub, id: qualify(scope, sub.id),
        ...(sub.from ? { from: resolvePort(file, scope, sub.from, 'outputs') }
          : { from_topic: scope.length ? scope.join('.') + '/' + sub.from_topic : sub.from_topic }),
        to: resolvePort(file, scope, sub.to, 'inputs'), _model: file, _index: index })
    }
  }
  visit(project.model, [], new Set())
  for (const { file, scope, name, entry } of interfaces) {
    const local = name.split('.').at(-1)!
    for (const direction of ['inputs', 'outputs']) {
      for (const alias of Object.keys(entry[direction] || {})) {
        const target = resolvePort(file, scope, local + '.' + alias, direction)
        const separator = target.includes(':') ? ':' : '.'
        const index = target.indexOf(separator), component = components[target.slice(0, index)], port = target.slice(index + 1)
        const desc = component?.[direction]?.[port]
        if (desc) component[direction][port] = { ...desc, publicAliases: [...(desc.publicAliases || []), name + ':' + alias] }
      }
    }
  }
  return { ...graphModel(project), components, subscriptions, external_topics: external,
    expandedDefinitions: interfaces.map(({ name, entry, file, scope }) => ({ name, file: entry.definition, model: file, scope, localName: name.split('.').at(-1),
      interface: project.documents[entry.definition]?.value?.interface || {} })) }
}

/** Enumerate the complete authored subtree of one instance, guarding invalid cycles. */
export function definitionScopes(project: any, name: string): string[] {
  const parts = name.split('.')
  let file = project.model
  for (const part of parts.slice(0, -1)) {
    const entry = project.subsystems?.[file]?.[part]
    if (!entry) return []
    file = entry.model
  }
  const paths: string[] = []
  function visit(owner: string, local: string, path: string, seen: Set<string>) {
    const entry = project.subsystems?.[owner]?.[local]
    if (!entry || entry.unresolved || seen.has(entry.definition)) return
    paths.push(path)
    const children = project.documents[entry.model]?.value?.subsystems || {}
    for (const child of Object.keys(children)) visit(entry.model, child, path + '.' + child, new Set(seen).add(entry.definition))
  }
  visit(file, parts.at(-1)!, name, new Set())
  return paths
}
