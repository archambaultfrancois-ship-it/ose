import { afterEach, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import App from '../src/App.vue'
import { account } from '../src/api'
import { flowStream } from '../src/flow-live'

vi.mock('../src/components/ComponentsPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../src/components/LogsPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../src/components/MeasurementsPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../src/components/RecordingsPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../src/components/ParametersPage.vue', () => ({ default: { template: '<div />' } }))

let app: ReturnType<typeof createApp> | undefined
afterEach(() => {
  app?.unmount(); account.actor = ''; document.body.innerHTML = ''
  Object.assign(flowStream, { watchId: null, connected: false, update: null })
  vi.unstubAllGlobals()
})
it('subscribes and routes Live samples through the existing app WebSocket', async () => {
  const sockets: any[] = [], sent: any[] = []
  vi.stubGlobal('WebSocket', class {
    static OPEN = 1
    static CONNECTING = 0
    readyState = 0
    constructor() { sockets.push(this) }
    send(data: string) { sent.push(JSON.parse(data)) }
    close() { this.readyState = 3 }
  })
  vi.stubGlobal('fetch', vi.fn(async (path: string) => new Response(JSON.stringify(
    path.endsWith('/session') ? { actor: 'flow-transport', csrf: 'csrf', permissions: { 'flows.read': ['*'] } }
      : path.endsWith('/components') ? { components: [] } : { items: [], next: null },
  ))))
  const root = document.createElement('div'); document.body.append(root)
  app = createApp(App); app.mount(root)
  for (let n = 0; n < 100 && !sockets.length; n++) { await new Promise(resolve => setTimeout(resolve, 5)); await nextTick() }
  expect(sockets).toHaveLength(1)
  const socket = sockets[0]; socket.readyState = 1; socket.onopen(); await nextTick()
  flowStream.watchId = 'watch-one'; flowStream.intervalMs = 500; await nextTick()
  expect(sent).toContainEqual({ action: 'subscribe', subscription_id: 'flow', stream: 'flow', selection: ['watch-one'], interval_ms: 500 })
  const data = { watch_id: 'watch-one', status: 'waiting', sample: null }
  socket.onmessage({ data: JSON.stringify({ type: 'flow.sample', data }) })
  expect(flowStream.update).toEqual(data)
  flowStream.watchId = null; await nextTick()
  expect(sent.at(-1)).toEqual({ action: 'unsubscribe', subscription_id: 'flow' })
})
