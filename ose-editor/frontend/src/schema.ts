import { stringify } from 'lossless-json'

export function parameterShape(project: any, path: string[]) {
  if (path[0] !== 'shared_parameters' || path[2] !== 'initial') return null
  const model = project?.documents?.[project.model]?.value, block = model?.shared_parameters?.[path[1]]
  const validation = project?.validation
  if (!block || validation?.revision !== project.revision) return null
  const shape = validation.catalogues?.[block.schema || model.schemas]
  if (!shape) return null
  let type = block.root, dimensions: any[] = [], modifier = 'required'
  for (const segment of path.slice(3)) {
    if (/^\d+$/.test(segment)) { if (dimensions.length) dimensions = dimensions.slice(1); else modifier = 'required'; continue }
    const field = shape.messages[type]?.find((item: any) => item.name === segment)
    if (!field) return null
    type = field.type; dimensions = field.dimensions || []; modifier = field.modifier
  }
  function initial(name: string, dims: number[] = [], cardinality = 'required', depth = 0): any {
    if (depth > 32 || cardinality === 'optional') return null
    if (['repeated', 'packed'].includes(cardinality)) return []
    if (dims.length) { if (dims[0] > 256) return []; return Array.from({ length: dims[0] }, () => initial(name, dims.slice(1), 'required', depth + 1)) }
    if (shape.messages[name]) return Object.fromEntries(shape.messages[name].map((field: any) => [field.name, initial(field.type, field.dimensions, field.modifier, depth + 1)]))
    if (shape.enums[name]) return Object.values(shape.enums[name])[0]
    if (name === 'string') return ''
    if (name === 'bool') return false
    if (name === 'c32' || name === 'c64') return { real: 0, imag: 0 }
    return 0
  }
  return { type, modifier, dimensions, enums: shape.enums[type] ? Object.values(shape.enums[type]) as string[] : null,
    suggestions: Object.fromEntries((shape.messages[type] || []).map((field: any) => [field.name, stringify(initial(field.type, field.dimensions, field.modifier))])) }
}
