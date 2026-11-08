export function layoutGraph(nodes: any[], edges: any[]) {
  return {
    id: 'root',
    layoutOptions: { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.spacing.nodeNode': '70', 'elk.layered.spacing.nodeNodeBetweenLayers': '100' },
    children: nodes.map(node => ({
      id: node.id, width: 270, height: 80 + (node.data.inputs.length + node.data.outputs.length) * 34,
      layoutOptions: { 'elk.portConstraints': 'FIXED_ORDER' },
      // ELK numbers ports clockwise: west-side inputs run bottom to top,
      // whereas our component rows are rendered top to bottom on both sides.
      ports: [
        ...node.data.inputs.map((p: any, index: number) => ({ id: node.id + ':' + p.endpoint, width: 8, height: 8,
          properties: { 'port.side': 'WEST', 'port.index': String(node.data.inputs.length - 1 - index) } })),
        ...node.data.outputs.map((p: any, index: number) => ({ id: node.id + ':' + p.endpoint, width: 8, height: 8,
          properties: { 'port.side': 'EAST', 'port.index': String(node.data.inputs.length + index) } })),
      ],
    })),
    edges: edges.map(edge => ({ id: edge.id, sources: [edge.source + ':' + edge.sourceHandle], targets: [edge.target + ':' + edge.targetHandle] })),
  }
}
