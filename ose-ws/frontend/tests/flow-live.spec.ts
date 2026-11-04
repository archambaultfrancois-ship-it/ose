import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import FlowLive from '../src/components/FlowLive.vue'
import { account } from '../src/api'
import { flowStream } from '../src/flow-live'

let app: ReturnType<typeof createApp> | undefined
let calls: Array<{ path: string; method: string; body?: any }>
let count: number
beforeEach(() => {
  calls = []; count = 0
  Object.assign(account, { actor: 'live-test', csrf: 'csrf', permissions: { 'flows.read': ['source.*'] } })
  Object.assign(flowStream, { watchId: null, connected: true, update: null, intervalMs: 500 })
  vi.stubGlobal('fetch', vi.fn(async (path: string, options: any) => {
    const body = options.body ? JSON.parse(options.body) : undefined
    calls.push({ path, method: options.method, body })
    if (options.method === 'DELETE') return new Response(null, { status: 204 })
    const data = options.method === 'POST' ? { id: body.id || 'watch-' + ++count, lease_s: 60 }
      : { watch_id: path.split('/').at(-1), epoch: 'epoch-one', status: 'waiting', sample: null }
    return new Response(JSON.stringify(data))
  }))
})
afterEach(async () => {
  app?.unmount(); app = undefined
  await nextTick()
  document.body.innerHTML = ''
  account.actor = ''
  Object.assign(flowStream, { watchId: null, connected: false, update: null })
  vi.restoreAllMocks(); vi.unstubAllGlobals()
})
function mount(props = ref<any>({ subscription: 'signal', epoch: 'epoch-one', active: true, connected: true, frozen: false })) {
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(FlowLive, props.value) }); app.mount(root)
  return { root, props, button: () => root.querySelector('button') as HTMLButtonElement }
}
async function until(predicate: () => boolean) {
  for (let n = 0; n < 100 && !predicate(); n++) { await new Promise(resolve => setTimeout(resolve, 5)); await nextTick() }
  expect(predicate()).toBe(true)
}
function sample(id: string) {
  return { watch_id: id, epoch: 'epoch-one', status: 'live', age_ms: 100, sample: {
    generation: 'generation-one', source_sequence: '42', value: { kind: 'message', name: 'Signal', type: 'Signal', children: [
      { name: 'counter', kind: 'value', type: 'uint64', value: '18446744073709551615' },
      { name: 'samples', kind: 'array', type: 'fl32', length: 16384, offset: 0, truncated: true,
        children: [{ name: '[0]', kind: 'value', type: 'fl32', value: 0.125 }] },
    ] },
  } }
}

it('starts only on Live, renders exact read-only values and releases on Stop', async () => {
  const { root, button } = mount()
  expect(calls).toHaveLength(0)
  button().click()
  await until(() => flowStream.watchId === 'watch-1')
  expect(calls[0]).toMatchObject({ method: 'POST', body: { subscription: 'signal', epoch: 'epoch-one', interval_ms: 500 } })
  flowStream.update = sample('watch-1'); await nextTick()
  expect(root.textContent).toContain('18446744073709551615')
  expect(root.textContent).toContain('16384 items')
  expect(root.textContent).toContain('truncated')
  expect(root.textContent).toContain('generation-one')
  expect(root.querySelectorAll('.live-sample input')).toHaveLength(0)
  button().click()
  await until(() => calls.some(call => call.method === 'DELETE' && call.path.endsWith('/watch-1')))
  expect(flowStream.watchId).toBeNull()
  expect(root.textContent).toContain('Stopped')
  expect(root.textContent).toContain('18446744073709551615')
})

it('releases and resumes when navigating, freezing or hiding, and stops on disconnect and logout', async () => {
  const { root, props, button } = mount()
  button().click(); await until(() => flowStream.watchId === 'watch-1')
  props.value.active = false
  await until(() => flowStream.watchId === null)
  expect(root.textContent).toContain('Paused')
  props.value.active = true; await until(() => flowStream.watchId === 'watch-2')
  props.value.frozen = true; await until(() => flowStream.watchId === null)
  props.value.frozen = false; await until(() => flowStream.watchId === 'watch-3')
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
  document.dispatchEvent(new Event('visibilitychange')); await until(() => flowStream.watchId === null)
  hidden.mockReturnValue(false); document.dispatchEvent(new Event('visibilitychange'))
  await until(() => flowStream.watchId === 'watch-4')
  flowStream.connected = false; await until(() => flowStream.watchId === null)
  flowStream.connected = true; await until(() => flowStream.watchId === 'watch-5')
  account.actor = ''; await until(() => flowStream.watchId === null)
  expect(calls.filter(call => call.method === 'DELETE')).toHaveLength(5)
})

it('releases a late create response after Stop and never attaches it to another selection', async () => {
  let resolve: (response: Response) => void = () => {}
  vi.mocked(fetch).mockImplementationOnce(() => new Promise<Response>(done => { resolve = done }))
  const { props, button } = mount()
  button().click(); await nextTick(); await nextTick()
  props.value = { subscription: 'other', epoch: 'epoch-two', active: true, connected: true }
  await nextTick()
  resolve(new Response(JSON.stringify({ id: 'late-watch', lease_s: 60 })))
  await until(() => calls.some(call => call.method === 'DELETE' && call.path.endsWith('/late-watch')))
  expect(flowStream.watchId).toBeNull()
  expect(calls.some(call => call.method === 'GET')).toBe(false)
})

it('renews cadence and array offset on the existing watch and rejects stale samples', async () => {
  const { root, button } = mount()
  button().click(); await until(() => flowStream.watchId === 'watch-1')
  const select = root.querySelector('select') as HTMLSelectElement
  select.value = '1000'; select.dispatchEvent(new Event('change', { bubbles: true }))
  await until(() => calls.some(call => call.body?.id === 'watch-1' && call.body.interval_ms === 1000))
  const input = root.querySelector('input') as HTMLInputElement
  input.value = '32'; input.dispatchEvent(new Event('input', { bubbles: true }))
  await until(() => calls.some(call => call.body?.id === 'watch-1' && call.body.array_offset === 32))
  flowStream.update = sample('old-watch'); await nextTick()
  expect(root.querySelector('.live-sample')).toBeNull()
  flowStream.update = sample('watch-1'); await nextTick()
  expect(root.querySelector('.live-sample')).toBeTruthy()
  flowStream.update = { watch_id: 'watch-1', status: 'unavailable', error: 'Source changed; restart Live.', sample: null }
  await until(() => flowStream.watchId === null)
  expect(root.textContent).toContain('Source changed; restart Live.')
  expect(root.textContent).toContain('18446744073709551615')
})

it('disables Live without payload permission', () => {
  account.permissions = { 'components.read': ['*'] }
  const { root, button } = mount()
  expect(button().disabled).toBe(true)
  expect(root.textContent).toContain('flows.read')
  expect(calls).toHaveLength(0)
})

it('coalesces rapid array edits and does not replace a newer sample with an older HTTP reply', async () => {
  const { root, button } = mount()
  button().click(); await until(() => flowStream.watchId === 'watch-1')
  let resolve: (response: Response) => void = () => {}
  vi.mocked(fetch).mockImplementationOnce(() => new Promise<Response>(done => { resolve = done }))
  const input = root.querySelector('input') as HTMLInputElement
  input.value = '3'; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
  input.value = '32'; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
  resolve(new Response(JSON.stringify({ id: 'watch-1', lease_s: 60 })))
  await until(() => calls.some(call => call.body?.id === 'watch-1' && call.body.array_offset === 32))
  flowStream.update = { ...sample('watch-1'), version: '2' }; await nextTick()
  flowStream.update = { ...sample('watch-1'), version: '1', sample: { ...sample('watch-1').sample, source_sequence: '1' } }; await nextTick()
  expect(root.textContent).toContain('Source sequence 42')
})
