/** Defaults and immutable edits for dynamic SDL schemas. Integers stay exact. */
export function copyValue(value) {
  // Web values already encode 64-bit integers as strings. JSON copying also
  // handles Vue reactive proxies, which structuredClone cannot clone.
  return JSON.parse(JSON.stringify(value))
}
export function makeTree(items, field, separator) {
  const root = { children: new Map() }
  for (const item of items) {
    let parent = root
    for (const label of item[field].split(separator)) {
      if (!parent.children.has(label)) parent.children.set(label, { label, children: new Map(), item: null })
      parent = parent.children.get(label)
    }
    parent.item = item
  }
  function nodes(parent) {
    return [...parent.children.values()].sort((a, b) => a.label.localeCompare(b.label)).map(node => ({ ...node, children: nodes(node) }))
  }
  return nodes(root)
}
export function presentationAt(schema, pointer) {
  const entries = schema.presentation || {}
  if (entries[pointer]) return entries[pointer]
  const segments = pointer.split('/')
  return Object.entries(entries).find(([path]) => {
    const pattern = path.split('/')
    return pattern.length === segments.length && pattern.every((part, i) => part === segments[i] || part === '*' && /^\d+$/.test(segments[i]))
  })?.[1] || {}
}
export function defaultValue(schema, type, dimensions = [], depth = 0) {
  if (depth > 32) throw new Error('Expand this structure in smaller steps.')
  if (dimensions.length) {
    if (dimensions.reduce((total, n) => total * n, 1) > 10000) throw new Error('This array is too large to initialize in the browser; import a value instead.')
    return Array.from({ length: dimensions[0] }, () => defaultValue(schema, type, dimensions.slice(1), depth + 1))
  }
  const shape = schema.types[type]
  if (shape?.kind === 'message') return Object.fromEntries(shape.fields.map(field => [field.name,
    field.cardinality === 'optional' ? null : ['repeated', 'packed'].includes(field.cardinality) ? [] : defaultValue(schema, field.type, field.dimensions, depth + 1)]))
  if (shape?.kind === 'enum') return shape.values[0]?.name ?? ''
  if (type === 'bool') return false
  if (type === 'string') return ''
  if (['c32', 'c64'].includes(type)) return { real: 0, imag: 0 }
  if (['int64', 'uint64'].includes(type)) return '0'
  return 0
}
export function inputValue(type, text) {
  if (['int64', 'uint64', 'string'].includes(type)) return text
  if (text.trim() === '') return ''
  const value = Number(text)
  return Number.isFinite(value) ? value : text
}
export function changes(before, after, pointer = '') {
  if (JSON.stringify(before) === JSON.stringify(after)) return []
  if (before !== null && after !== null && typeof before === 'object' && typeof after === 'object' && Array.isArray(before) === Array.isArray(after)) {
    return [...new Set([...Object.keys(before), ...Object.keys(after)])].flatMap(key =>
      changes(before[key], after[key], pointer + '/' + key.replaceAll('~', '~0').replaceAll('/', '~1')))
  }
  return [{ pointer: pointer || '/', before, after }]
}
