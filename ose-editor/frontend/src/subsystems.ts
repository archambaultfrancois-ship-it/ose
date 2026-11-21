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
