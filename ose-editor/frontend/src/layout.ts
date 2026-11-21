import { nodeSize } from './graph'

export function layoutGraph(nodes: any[], edges: any[]) {
  const options = { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT',
    'elk.hierarchyHandling': 'INCLUDE_CHILDREN', 'elk.spacing.nodeNode': '40',
    'elk.layered.spacing.nodeNodeBetweenLayers': '70' }
  const containers = new Map<string, any>()
  for (const node of nodes.filter(node => node.data.kind === 'definition')) {
    containers.set(node.data.name, { id: node.id, width: 344, height: 244, children: [],
      layoutOptions: { ...options, 'elk.padding': '[top=100,left=24,bottom=24,right=24]',
        'elk.nodeSize.constraints': 'MINIMUM_SIZE', 'elk.nodeSize.minimum': '(344,244)' } })
  }
  const children: any[] = []
  for (const node of nodes.filter(node => node.data.kind === 'definition')) {
    const parent = containers.get((node.data.scope || []).join('.'))
    const siblings = parent ? parent.children : children
    siblings.push(containers.get(node.data.name))
  }
  const leaves = nodes.filter(node => node.data.kind !== 'definition').map(node => ({
      id: node.id, ...nodeSize(node),
      layoutOptions: { 'elk.portConstraints': 'FIXED_ORDER' },
      // ELK numbers ports clockwise: west-side inputs run bottom to top,
      // whereas our component rows are rendered top to bottom on both sides.
      ports: [
        ...node.data.inputs.map((p: any, index: number) => ({ id: node.id + ':' + p.endpoint, width: 8, height: 8,
          properties: { 'port.side': 'WEST', 'port.index': String(node.data.inputs.length - 1 - index) } })),
        ...node.data.outputs.map((p: any, index: number) => ({ id: node.id + ':' + p.endpoint, width: 8, height: 8,
          properties: { 'port.side': 'EAST', 'port.index': String(node.data.inputs.length + index) } })),
      ],
    }))
  for (const leaf of leaves) {
    const source = nodes.find(node => node.id === leaf.id)!
    const parent = containers.get((source.data.scope || []).join('.'))
    const siblings = parent ? parent.children : children
    siblings.push(leaf)
  }
  return { id: 'root', layoutOptions: options, children,
    edges: edges.map(edge => ({ id: edge.id, sources: [edge.source + ':' + edge.sourceHandle], targets: [edge.target + ':' + edge.targetHandle] })),
  }
}


/** ELK compound coordinates are relative to their owning subsystem. */
export function layoutPositions(graph: any): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {}
  function visit(parent: any, x: number, y: number) {
    for (const child of parent.children || []) {
      const left = x + (child.x || 0), top = y + (child.y || 0)
      const frame = child.id.startsWith('definition:')
      const name = frame ? child.id.slice(11)
        : child.id.startsWith('component:') || child.id.startsWith('subsystem:') ? child.id.slice(10) : child.id
      positions[name] = { x: left + (frame ? 24 : 0), y: top + (frame ? 100 : 0) }
      visit(child, left, top)
    }
  }
  visit(graph, 0, 0)
  return positions
}

export type FrameBounds = { x: number; y: number; width: number; height: number }
export type FrameLayout = { positions: Record<string, { x: number; y: number }>; frameBounds: Record<string, FrameBounds> }

/** Reflow only this definition; nested frames move as intact containers. */
export function resizeLayout(nodes: any[], name: string, requested: FrameBounds, corner = 'se'): FrameLayout {
  const children = nodes.filter(node => (node.data.scope || []).join('.') === name)
  const size = nodeSize
  const sizes = children.map(size), gap = 24, padding = 24, heading = 100
  const candidates = Array.from({ length: Math.max(children.length, 1) }, (_, index) => {
    const columns = index + 1, rows = Math.max(1, Math.ceil(children.length / columns))
    const widths = Array.from({ length: columns }, () => 0), heights = Array.from({ length: rows }, () => 0)
    sizes.forEach((item, i) => {
      widths[i % columns] = Math.max(widths[i % columns], item.width)
      heights[Math.floor(i / columns)] = Math.max(heights[Math.floor(i / columns)], item.height)
    })
    const width = Math.max(344, 2 * padding + widths.reduce((a, b) => a + b, 0) + gap * (columns - 1))
    const height = Math.max(244, heading + padding + heights.reduce((a, b) => a + b, 0) + gap * (rows - 1))
    const overflow = Math.max(1, width / requested.width) * Math.max(1, height / requested.height)
    const aspect = Math.abs(Math.log((width / height) / (requested.width / requested.height)))
    return { columns, rows, widths, heights, width, height, overflow, aspect }
  })
  candidates.sort((a, b) => a.overflow - b.overflow || a.aspect - b.aspect)
  const grid = candidates[0], width = Math.max(requested.width, grid.width), height = Math.max(requested.height, grid.height)
  const bounds = { x: requested.x + (corner.includes('w') ? requested.width - width : 0),
    y: requested.y + (corner.includes('n') ? requested.height - height : 0), width, height }
  const result: FrameLayout = { positions: {}, frameBounds: { [name]: bounds } }
  function place(node: any, x: number, y: number) {
    const frame = node.data.kind === 'definition'
    const key = frame || node.data.kind === 'component' || node.data.subsystem ? node.data.name : node.id
    result.positions[key] = { x: x + (frame ? padding : 0), y: y + (frame ? heading : 0) }
    if (frame) result.frameBounds[key] = { x, y, ...size(node) }
  }
  const frame = nodes.find(node => node.data.kind === 'definition' && node.data.name === name)
  if (frame) result.positions[name] = { x: bounds.x + padding, y: bounds.y + heading }
  const horizontal = (width - 2 * padding - grid.widths.reduce((a, b) => a + b, 0)) / Math.max(1, grid.columns - 1)
  const vertical = (height - heading - padding - grid.heights.reduce((a, b) => a + b, 0)) / Math.max(1, grid.rows - 1)
  children.forEach((child, i) => {
    const column = i % grid.columns, row = Math.floor(i / grid.columns)
    const x = bounds.x + padding + grid.widths.slice(0, column).reduce((a, b) => a + b, 0) + horizontal * column
      + (grid.columns === 1 ? horizontal / 2 : 0)
    const y = bounds.y + heading + grid.heights.slice(0, row).reduce((a, b) => a + b, 0) + vertical * row
      + (grid.rows === 1 ? vertical / 2 : 0)
    place(child, x, y)
    if (child.data.kind === 'definition') {
      const dx = x - child.position.x, dy = y - child.position.y
      for (const descendant of nodes.filter(node => node.data.name.startsWith(child.data.name + '.'))) {
        place(descendant, descendant.position.x + dx, descendant.position.y + dy)
      }
    }
  })
  return result
}
