// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { getBezierPath, getSmoothStepPath, getStraightPath, Position } from '@vue-flow/core'
import { automaticRoute, roundedRoute, segmentBlocked, type Point, type RoutingNode } from '../src/automatic-routing'
import AutomaticEdge from '../src/components/AutomaticEdge.vue'

const node = (id: string, x: number, y = 0, width = 400, height = 200): RoutingNode => ({ id, name: id, x, y, width, height, frame: false })
function avoids(points: Point[], boxes: RoutingNode[]) {
  for (let i = 1; i < points.length; i++) expect(segmentBlocked(points[i - 1], points[i], boxes)).toBe(false)
}

describe('local automatic loopback routing', () => {
  it('wraps a self-loop around its own card and ignores remote nodes', () => {
    const card = node('self', 0), source = { x: 403, y: 70 }, target = { x: -3, y: 110 }
    const points = automaticRoute(source, target, 'self', 'self', [card])!
    expect(points).not.toBeNull()
    expect(points[0]).toEqual(source)
    expect(points.at(-1)).toEqual(target)
    avoids(points, [card])
    expect(points.some(point => point.y < 0 || point.y > 200)).toBe(true)
    expect(points.every(point => point.x >= -32 && point.x <= 432 && point.y >= -32 && point.y <= 232)).toBe(true)
    expect(automaticRoute(source, target, 'self', 'self', [card, node('remote', 10000, -5000)])).toEqual(points)
    for (const radius of [0, 8, 20]) expect(roundedRoute(points, radius).path).not.toMatch(/NaN|Infinity/)
  })

  it('routes feedback around the local chain while staying inside its expanded subsystem', () => {
    const children = ['first', 'second', 'third', 'last'].map((name, i) => node('cell.' + name, i * 440))
    const frame: RoutingNode = { ...node('definition:cell', -24, -100, 1768, 324), name: 'cell', frame: true }
    const points = automaticRoute({ x: 1723, y: 100 }, { x: -3, y: 120 }, 'cell.last', 'cell.first', [...children, frame])!
    expect(points).not.toBeNull()
    avoids(points, children)
    for (const point of points) {
      expect(point.x).toBeGreaterThanOrEqual(frame.x)
      expect(point.x).toBeLessThanOrEqual(frame.x + frame.width)
      expect(point.y).toBeGreaterThanOrEqual(frame.y + 80)
      expect(point.y).toBeLessThanOrEqual(frame.y + frame.height)
    }
  })

  it('avoids unrelated subsystem frames and widens its local search only when needed', () => {
    const source = node('source', 800), target = node('target', 0, 0, 200)
    const frame: RoutingNode = { ...node('definition:other', 350, -30, 200, 400), name: 'other', frame: true }
    const nodes = [source, target, frame, node('other.worker', 370, 0, 150, 200)]
    const points = automaticRoute({ x: 1203, y: 100 }, { x: -3, y: 120 }, 'source', 'target', nodes)!
    expect(points).not.toBeNull()
    avoids(points, nodes)
    expect(points.every(point => point.y >= -96 && point.y <= 296)).toBe(true)
    const moved = nodes.map(item => item.id === 'source' ? { ...item, x: 1000, y: 80 } : item)
    const updated = automaticRoute({ x: 1403, y: 180 }, { x: -3, y: 120 }, 'source', 'target', moved)!
    expect(updated).not.toEqual(points)
    avoids(updated, moved)
  })

  it.each(['source', 'target', 'both'])('routes an external return outside its %s frame', side => {
    const from = node('right.worker', 800), to = node('left.worker', 0)
    const sourceFrame: RoutingNode = { ...node('definition:right', 776, -100, 448, 324), name: 'right', frame: true }
    const targetFrame: RoutingNode = { ...node('definition:left', -24, -100, 448, 324), name: 'left', frame: true }
    const frames = side === 'source' ? [sourceFrame] : side === 'target' ? [targetFrame] : [sourceFrame, targetFrame]
    const points = automaticRoute({ x: 1203, y: 70 }, { x: -3, y: 110 }, from.id, to.id, [from, to, ...frames])!
    expect(points).not.toBeNull()
    avoids(points, [from, to])
    const exit = side === 'target' ? 0 : points.findIndex(point => point.x > sourceFrame.x + sourceFrame.width)
    const entry = side === 'source' ? points.length - 1 : points.findLastIndex(point => point.x < targetFrame.x)
    expect(exit).toBeGreaterThanOrEqual(0)
    expect(entry).toBeGreaterThan(exit)
    avoids(points.slice(exit, entry + 1), frames)
    if (side !== 'target') avoids(points.slice(exit), [sourceFrame])
    if (side !== 'source') avoids(points.slice(0, entry + 1), [targetFrame])
  })

  it('goes outside nested endpoint frames while remaining in their common parent', () => {
    const from = node('parent.right.worker', 800), to = node('parent.left.worker', 0)
    const sourceFrame: RoutingNode = { ...node('definition:parent.right', 776, -100, 448, 324), name: 'parent.right', frame: true }
    const targetFrame: RoutingNode = { ...node('definition:parent.left', -24, -100, 448, 324), name: 'parent.left', frame: true }
    const parent: RoutingNode = { ...node('definition:parent', -48, -200, 1296, 448), name: 'parent', frame: true }
    const points = automaticRoute({ x: 1203, y: 70 }, { x: -3, y: 110 }, from.id, to.id, [from, to, sourceFrame, targetFrame, parent])!
    expect(points).not.toBeNull()
    const exit = points.findIndex(point => point.x > sourceFrame.x + sourceFrame.width)
    const entry = points.findLastIndex(point => point.x < targetFrame.x)
    avoids(points.slice(exit, entry + 1), [sourceFrame, targetFrame])
    expect(points.every(point => point.x >= parent.x && point.x <= parent.x + parent.width && point.y >= parent.y + 80 && point.y <= parent.y + parent.height)).toBe(true)
  })

  it('keeps normal forward paths and marker styles, with no route-editing controls', async () => {
    const source = node('source', 0), target = node('target', 800)
    const props = { id: 'link', type: 'default', source: 'source', target: 'target', sourceX: 403, sourceY: 70,
      targetX: 797, targetY: 110, sourcePosition: Position.Right, targetPosition: Position.Left, sourceNode: {}, targetNode: {},
      markerStart: '', markerEnd: 'url(#arrow)', events: {}, data: {}, selected: true, nodes: [source, target] }
    const wrapper = mount(AutomaticEdge, { props: props as any })
    try {
      for (const [type, path] of [['default', getBezierPath], ['straight', getStraightPath], ['smoothstep', getSmoothStepPath]] as const) {
        await wrapper.setProps({ type })
        expect(wrapper.get('.vue-flow__edge-path').attributes('d')).toBe(path(props)[0])
      }
      await wrapper.setProps({ sourceX: 1203, targetX: -3, nodes: [node('source', 800), node('target', 0)] })
      expect(wrapper.get('.vue-flow__edge-path').attributes('d')).toContain('Q')
      expect(wrapper.get('.vue-flow__edge-path').attributes('marker-end')).toBe('url(#arrow)')
      expect(wrapper.find('[role="button"]').exists()).toBe(false)
      expect(wrapper.find('.route-point').exists()).toBe(false)
    } finally { wrapper.unmount() }
  })

  it('returns no invented route when overlapping cards obstruct a port', () => {
    expect(automaticRoute({ x: 403, y: 70 }, { x: -3, y: 110 }, 'small', 'large',
      [node('small', 0), node('large', 0, 0, 500)])).toBeNull()
  })
})
