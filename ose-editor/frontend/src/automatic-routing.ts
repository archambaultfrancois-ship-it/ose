export type Point = { x: number; y: number }
export type Box = Point & { width: number; height: number }
export type RoutingNode = Box & { id: string; name: string; frame: boolean }
const clearance = 8, stub = 14, epsilon = 0.01
const right = (box: Box) => box.x + box.width
const bottom = (box: Box) => box.y + box.height
const inflate = (box: Box, margin: number): Box => ({ x: box.x - margin, y: box.y - margin, width: box.width + 2 * margin, height: box.height + 2 * margin })
const inside = (point: Point, box: Box) => point.x > box.x + epsilon && point.x < right(box) - epsilon && point.y > box.y + epsilon && point.y < bottom(box) - epsilon
const overlaps = (a: Box, b: Box) => a.x < right(b) && right(a) > b.x && a.y < bottom(b) && bottom(a) > b.y
const contains = (frame: RoutingNode, node: RoutingNode) => node.name.startsWith(frame.name + '.')

export function segmentBlocked(a: Point, b: Point, boxes: Box[]): boolean {
  return boxes.some(box => {
    // Clip the segment against the open rectangle, allowing travel on its boundary.
    let low = 0, high = 1
    for (const [start, delta, min, max] of [[a.x, b.x - a.x, box.x + epsilon, right(box) - epsilon],
      [a.y, b.y - a.y, box.y + epsilon, bottom(box) - epsilon]]) {
      if (Math.abs(delta) < epsilon) { if (start < min || start > max) return false }
      else {
        const first = (min - start) / delta, last = (max - start) / delta
        low = Math.max(low, Math.min(first, last)); high = Math.min(high, Math.max(first, last))
        if (low > high) return false
      }
    }
    return low <= high
  })
}

class Queue {
  private items: { key: number; cost: number; priority: number }[] = []
  get length() { return this.items.length }
  push(item: { key: number; cost: number; priority: number }) {
    let index = this.items.length
    this.items.push(item)
    while (index) {
      const parent = (index - 1) >> 1
      if (this.items[parent].priority <= item.priority) break
      this.items[index] = this.items[parent]; index = parent
    }
    this.items[index] = item
  }
  pop() {
    const first = this.items[0], last = this.items.pop()!
    if (this.items.length) {
      let index = 0
      while (index * 2 + 1 < this.items.length) {
        let child = index * 2 + 1
        if (child + 1 < this.items.length && this.items[child + 1].priority < this.items[child].priority) child++
        if (last.priority <= this.items[child].priority) break
        this.items[index] = this.items[child]; index = child
      }
      this.items[index] = last
    }
    return first
  }
}

function simplify(points: Point[]): Point[] {
  const result: Point[] = []
  for (const point of points) {
    const last = result.at(-1)
    if (last && Math.hypot(point.x - last.x, point.y - last.y) < epsilon) continue
    const previous = result.at(-2)
    if (previous && last && ((previous.x === last.x && last.x === point.x && (last.y - previous.y) * (point.y - last.y) >= 0)
      || (previous.y === last.y && last.y === point.y && (last.x - previous.x) * (point.x - last.x) >= 0))) result.pop()
    result.push(point)
  }
  return result
}

/** Manhattan A*: local obstacle boundaries provide the useful routing corridors. */
function search(start: Point, end: Point, obstacles: Box[], area: Box): Point[] | null {
  const within = (point: Point) => point.x >= area.x && point.x <= right(area) && point.y >= area.y && point.y <= bottom(area)
  const boxes = obstacles.filter(box => overlaps(box, area))
  if (!within(start) || !within(end) || boxes.some(box => inside(start, box) || inside(end, box))) return null
  const xs = [...new Set([start.x, end.x, area.x, right(area), ...boxes.flatMap(box => [box.x, right(box)])])]
    .filter(x => x >= area.x && x <= right(area)).sort((a, b) => a - b)
  const ys = [...new Set([start.y, end.y, area.y, bottom(area), ...boxes.flatMap(box => [box.y, bottom(box)])])]
    .filter(y => y >= area.y && y <= bottom(area)).sort((a, b) => a - b)
  const width = xs.length, origin = ys.indexOf(start.y) * width + xs.indexOf(start.x), destination = ys.indexOf(end.y) * width + xs.indexOf(end.x)
  const point = (index: number): Point => ({ x: xs[index % width], y: ys[Math.floor(index / width)] })
  const queue = new Queue(), costs = new Map<number, number>(), previous = new Map<number, number>(), blocked = new Map<string, boolean>()
  const first = origin * 4 // direction 0: leave the output towards the right.
  queue.push({ key: first, cost: 0, priority: Math.abs(start.x - end.x) + Math.abs(start.y - end.y) }); costs.set(first, 0)
  while (queue.length) {
    const current = queue.pop()
    if (costs.get(current.key) !== current.cost) continue
    const index = Math.floor(current.key / 4), direction = current.key % 4, a = point(index), column = index % width, row = Math.floor(index / width)
    if (index === destination) {
      const points = [end]
      let key = current.key
      while (previous.has(key)) { key = previous.get(key)!; points.push(point(Math.floor(key / 4))) }
      return simplify(points.reverse())
    }
    for (const [dx, dy, nextDirection] of [[1, 0, 0], [0, 1, 1], [-1, 0, 2], [0, -1, 3]]) {
      if ((index === origin && nextDirection === 2) || column + dx < 0 || column + dx >= width || row + dy < 0 || row + dy >= ys.length) continue
      const next = (row + dy) * width + column + dx, b = point(next)
      if (next === destination && nextDirection === 2) continue // no U-turn immediately before the input.
      const segment = index < next ? index + ':' + next : next + ':' + index
      if (!blocked.has(segment)) blocked.set(segment, segmentBlocked(a, b, boxes))
      if (blocked.get(segment)) continue
      const cost = current.cost + Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + (direction === nextDirection ? 0 : 12)
      const key = next * 4 + nextDirection
      if (cost >= (costs.get(key) ?? Infinity)) continue
      costs.set(key, cost); previous.set(key, current.key)
      queue.push({ key, cost, priority: cost + Math.abs(b.x - end.x) + Math.abs(b.y - end.y) })
    }
  }
  return null
}

function obstaclesFor(nodes: RoutingNode[], frames: RoutingNode[]): Box[] {
  const barriers = frames.filter(frame => !frames.some(parent => parent !== frame && contains(parent, frame)))
  return [...nodes.filter(node => !node.frame && !barriers.some(frame => contains(frame, node))), ...barriers].map(node => inflate(node, clearance))
}

function routeBetween(start: Point, end: Point, from: Box, to: Box, obstacles: Box[], common?: RoutingNode): Point[] | null {
  if (common) {
    const area = { x: common.x + 4, y: common.y + 80, width: common.width - 8, height: common.height - 84 }
    const route = search(start, end, obstacles, area)
    if (route) return route
  }
  const pair = { x: Math.min(from.x, to.x, start.x, end.x), y: Math.min(from.y, to.y, start.y, end.y),
    width: Math.max(right(from), right(to), start.x, end.x) - Math.min(from.x, to.x, start.x, end.x),
    height: Math.max(bottom(from), bottom(to), start.y, end.y) - Math.min(from.y, to.y, start.y, end.y) }
  // Widen only when necessary: a remote node must not send a local loop around the entire application.
  for (const margin of [32, 96, 256]) {
    const route = search(start, end, obstacles, inflate(pair, margin))
    if (route) return route
  }
  return null
}

/** A null result lets the normal renderer handle an overlapping/unroutable layout. */
export function automaticRoute(source: Point, target: Point, sourceId: string, targetId: string, nodes: RoutingNode[]): Point[] | null {
  const from = nodes.find(node => node.id === sourceId), to = nodes.find(node => node.id === targetId)
  if (!from || !to) return null
  const frames = nodes.filter(node => node.frame)
  const common = frames.filter(frame => contains(frame, from) && contains(frame, to)).sort((a, b) => b.name.length - a.name.length)[0]
  // An external return treats the outermost exclusive frame as the endpoint.
  // Only its short connection to the actual port is allowed inside that frame.
  const sourceFrame = frames.filter(frame => contains(frame, from) && !contains(frame, to)).sort((a, b) => a.name.length - b.name.length)[0]
  const targetFrame = frames.filter(frame => contains(frame, to) && !contains(frame, from)).sort((a, b) => a.name.length - b.name.length)[0]
  const start = { x: source.x + stub, y: source.y }, end = { x: target.x - stub, y: target.y }
  const exit = sourceFrame ? { x: right(sourceFrame) + stub, y: source.y } : start
  const entry = targetFrame ? { x: targetFrame.x - stub, y: target.y } : end
  let departure: Point[] = [start], arrival: Point[] = [end]
  if (sourceFrame) {
    const area = { x: sourceFrame.x + 4, y: sourceFrame.y + 80, width: sourceFrame.width + stub - 4, height: sourceFrame.height - 84 }
    const leg = search(start, exit, obstaclesFor(nodes, frames.filter(frame => !contains(frame, from))), area)
    if (!leg) return null
    departure = leg
  }
  if (targetFrame) {
    const area = { x: targetFrame.x - stub, y: targetFrame.y + 80, width: targetFrame.width + stub - 4, height: targetFrame.height - 84 }
    const leg = search(entry, end, obstaclesFor(nodes, frames.filter(frame => !contains(frame, to))), area)
    if (!leg) return null
    arrival = leg
  }
  const barriers = frames.filter(frame => frame === sourceFrame || frame === targetFrame || !contains(frame, from) && !contains(frame, to))
  const route = routeBetween(exit, entry, sourceFrame || from, targetFrame || to, obstaclesFor(nodes, barriers), common)
  return route ? simplify([source, ...departure, ...route, ...arrival, target]) : null
}

export function roundedRoute(points: Point[], radius: number): { path: string; x: number; y: number } {
  let path = `M ${points[0].x} ${points[0].y}`
  let label = { x: points[0].x, y: points[0].y }, longest = -1
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], c = points[i + 1], length = Math.hypot(b.x - a.x, b.y - a.y)
    if (length > longest) { longest = length; label = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } }
    const afterLength = c ? Math.hypot(c.x - b.x, c.y - b.y) : 0, bend = Math.min(radius, length / 2, afterLength / 2)
    if (bend && c) path += ` L ${b.x + (a.x - b.x) * bend / length} ${b.y + (a.y - b.y) * bend / length} Q ${b.x} ${b.y} ${b.x + (c.x - b.x) * bend / afterLength} ${b.y + (c.y - b.y) * bend / afterLength}`
    else path += ` L ${b.x} ${b.y}`
  }
  return { path, ...label }
}
