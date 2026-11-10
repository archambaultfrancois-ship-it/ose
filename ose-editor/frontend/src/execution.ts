import { scalarText } from './hints'

export type ActivationLink = { input: string; event: 'message' | 'timeout'; output: string }
export const inputKind = (desc: any) => desc?.kind === 'timer' ? 'timer' : 'message'
export const inputMode = (desc: any) => inputKind(desc) === 'timer' ? 'timer' : desc?.mode === 'pull' ? 'pull' : 'push'
export function inputEvents(desc: any): ('message' | 'timeout')[] {
  if (inputKind(desc) === 'timer') return ['timeout']
  if (inputMode(desc) === 'pull') return []
  return desc?.timeout !== undefined ? ['message', 'timeout'] : ['message']
}
export function activationLinks(component: any): ActivationLink[] {
  return Object.entries(component?.inputs || {}).flatMap(([input, desc]: any) =>
    inputEvents(desc).flatMap(event => (Array.isArray(desc?.activates?.[event]) ? desc.activates[event] : [])
      .filter((output: any) => typeof output === 'string')
      .map((output: string) => ({ input, event, output }))))
}
export function inputLabel(desc: any): string {
  if (inputKind(desc) === 'timer') return (desc.timer?.mode === 'one_shot' ? 'Once' : 'Every') + ' ' + scalarText(desc.timer?.delay) + ' s'
  return inputMode(desc) === 'pull' ? 'Pull · passive' : 'Push' + (desc.timeout !== undefined ? ' · timeout ' + scalarText(desc.timeout) + ' s' : '')
}

// UI guidance only. The Python activation validator remains authoritative.
export function activationCycle(model: any): string[] | null {
  const graph = new Map<string, Set<string>>()
  function edge(from: string, to: string) {
    if (!graph.has(from)) graph.set(from, new Set())
    if (!graph.has(to)) graph.set(to, new Set())
    graph.get(from)!.add(to)
  }
  for (const [name, component] of Object.entries(model?.components || {}) as any) {
    for (const link of activationLinks(component)) edge(name + '.' + link.input + ':' + link.event, name + '.' + link.output + ':publish')
  }
  for (const sub of model?.subscriptions || []) {
    if (!sub.from || sub.from_topic) continue
    const [name, port] = (sub.to || '').split('.'), desc = model.components?.[name]?.inputs?.[port]
    if (desc && inputMode(desc) === 'push') edge(sub.from + ':publish', sub.to + ':message')
  }
  const done = new Set<string>(), active = new Map<string, number>(), path: string[] = []
  // Iterative DFS also handles large graphs without a browser call-stack limit.
  for (const root of graph.keys()) {
    if (done.has(root)) continue
    const stack: { key: string; children: Iterator<string> }[] = []
    const enter = (key: string) => { active.set(key, path.length); path.push(key); stack.push({ key, children: graph.get(key)!.values() }) }
    enter(root)
    while (stack.length) {
      const frame = stack.at(-1)!, next = frame.children.next()
      if (next.done) { stack.pop(); active.delete(frame.key); path.pop(); done.add(frame.key); continue }
      if (active.has(next.value)) return [...path.slice(active.get(next.value)!), next.value]
      if (!done.has(next.value)) enter(next.value)
    }
  }
  return null
}

