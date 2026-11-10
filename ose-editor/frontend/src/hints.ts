export function keysFor(role: string, path: string[], model?: any): Record<string, string> {
  const last = path.at(-1), depth = path.length
  if (role === 'model') {
    if (!depth) return { version: '2', schemas: '"schemas"', components: '{}', subscriptions: '[]', external_topics: '{}', shared_parameters: '{}' }
    if (path[0] === 'components' && depth === 2) return { inputs: '{}', outputs: '{}', parameters: '{}', parameter_bindings: '{}', recording_types: '{}' }
    if (path[2] === 'inputs' && depth === 4) {
      const desc = atPath(model, path)
      return desc?.kind === 'timer' ? { kind: '"timer"', timer: '{"mode":"periodic","delay":0.1}', activates: '{"timeout":[]}' }
        : { kind: '"message"', mode: '"push"', root: '""', schema: '""', ...(desc?.mode === 'pull' ? {} : { timeout: '0.5', activates: '{"message":[]}' }) }
    }
    if (path[0] === 'components' && ['outputs', 'recording_types'].includes(path[2]) && depth === 4) return { root: '""', schema: '""' }
    if (path[2] === 'inputs' && last === 'timer') return { mode: '"periodic"', delay: '0.1', start: '"automatic"' }
    if (path[2] === 'inputs' && last === 'activates') {
      const desc = atPath(model, path.slice(0, 4))
      return desc?.kind === 'timer' ? { timeout: '[]' } : desc?.mode === 'pull' ? {} : { message: '[]', ...(desc?.timeout !== undefined ? { timeout: '[]' } : {}) }
    }
    if (path[0] === 'external_topics' && depth === 2) return { root: '""', schema: '""' }
    if (path[2] === 'parameter_bindings' && depth === 4) return { path: '""', access: '"read"' }
    if (path[0] === 'shared_parameters' && depth === 2) return { root: '""', schema: '""', initial: '{}' }
    if (path[0] === 'subscriptions' && depth === 2) return { id: '""', from: '""', from_topic: '""', to: '""', qos: '{}' }
    if (last === 'qos') return { buffer: '"fifo"', capacity: '1024', overflow: '"reject_new"', ack: '"none"', retry: '"none"', wait_timeout_ms: '100', durability: '"memory"' }
    if (last === 'retry') return { max_retries: '3', ack_timeout_ms: '100', backoff_multiplier: '2' }
  }
  if (role === 'deployment') {
    if (!depth) return { version: '1', components: '{}', subscriptions: '{}', execution: '"local"', max_frame: '1048576', max_datagram: '1200', broker: '{}', process_names: '{}', parameter_clients: '{}' }
    if (path[0] === 'components' && depth === 2) return { command: '["{python}","handler.py"]', container: '{"language":"python","handler":"handler.py"}', build: '[]', cwd: '"."', env: '{}', placement: '"local"' }
    if (last === 'container') return { language: '"python"', handler: '""', class: '""', unit: '""' }
    if (last === 'placement') return { node_selector: '{}', resources: '{}' }
    if (last === 'resources') return { requests: '{}', limits: '{}' }
    if (path[0] === 'subscriptions' && depth === 2) return { transport: '"tcp"', routing: '"broker"' }
    if (last === 'broker') return { host: '"127.0.0.1"', external: '{}' }
    if (last === 'external') return { enabled: 'true', max_clients: '32', max_subscriptions: '256', max_capacity: '4096', max_topics: '1024', max_catalogues: '4096', max_wait_timeout_ms: '1000' }
    if (last === 'process_names') return { prefix: '"ose"', components: '{}' }
    if (path[0] === 'parameter_clients' && depth === 2) return { read: '[]', write: '[]', token: '""' }
  }
  if (role === 'schema') {
    if (!depth) return { format: '"sdl-schema"', version: '1', package: '""', doc: '""', constants: '{}', aliases: '{}', enums: '{}', messages: '{}' }
    if (path[0] === 'constants' && depth === 2) return { value: '1', doc: '""' }
    if (path[0] === 'aliases' && depth === 2) return { target: '"uint64"', doc: '""' }
    if (path[0] === 'messages' && depth === 2) return { fields: '[]', doc: '""' }
    if (path[0] === 'enums' && depth === 2) return { values: '[]', doc: '""' }
    if (path.includes('fields') && /^\d+$/.test(last || '')) return { name: '"value"', type: '"fl64"', id: '1', modifier: '"required"', dimensions: '[]', wire: '{}', doc: '""' }
    if (path.includes('values') && /^\d+$/.test(last || '')) return { name: '"READY"', value: '0', doc: '""' }
    if (last === 'wire') return { bits: '12', q: '"0.1"', encoding: '"varint"', align: '1', pad_after: '0' }
  }
  return {}
}
export const choices: Record<string, string[]> = {
  buffer: ['fifo', 'latest'], overflow: ['reject_new', 'drop_oldest', 'wait'], ack: ['none', 'received'],
  access: ['read', 'write', 'read_write'], language: ['python', 'c', 'rust', 'java', 'ada'], execution: ['local', 'distributed'],
  transport: ['tcp', 'udp'], routing: ['broker', 'direct'], modifier: ['required', 'optional', 'repeated', 'packed'], encoding: ['varint', 'zigzag'],
}
export const scalarText = (value: any): string => value?.$number ?? String(value ?? '')
export function choicesFor(role: string, path: string[], model?: any): string[] | undefined {
  if (role === 'model' && path[2] === 'inputs') {
    if (path.at(-1) === 'kind') return ['message', 'timer']
    if (path.at(-1) === 'start') return ['automatic', 'manual']
    if (path.at(-1) === 'mode') return path.includes('timer') ? ['periodic', 'one_shot'] : ['push', 'pull']
  }
  return choices[path.at(-1) || '']
}
export function nodeLabel(node: any): string {
  const fallback = node?.name || ''
  if (node?.path?.length !== 2 || node.path[0] !== 'subscriptions' || !/^\d+$/.test(node.path[1])) return fallback
  const id = node.children?.find((child: any) => child.name === 'id' && child.kind === 'string')
  if (id) {
    try { const value = JSON.parse(id.literal); if (typeof value === 'string' && value) return value } catch { /* Keep the index for incomplete sources. */ }
  }
  return fallback
}
export function atPath(root: any, path: string[] = []) { return path.reduce((value, key) => value?.[key], root) }
