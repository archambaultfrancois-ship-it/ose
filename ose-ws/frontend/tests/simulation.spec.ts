import { afterEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, h, reactive } from 'vue'
import { account } from '../src/api'
import SimulationPage from '../src/components/SimulationPage.vue'
import SimulationActions from '../src/components/SimulationActions.vue'
import { simulatedSeconds, secondsToNanoseconds } from '../src/simulation'

it('keeps virtual-time commands exact beyond the JavaScript integer range', () => {
  expect(secondsToNanoseconds('9007199.254740993')).toBe('9007199254740993')
  expect(simulatedSeconds('9007199254740993')).toBe('9007199.254740993')
  expect(simulatedSeconds('0')).toBe('0')
  expect(secondsToNanoseconds('18446744073.709551615')).toBe('18446744073709551615')
  for (const value of ['1e3', '-1', '0.0000000001', '18446744073.709551616', ''])
    expect(() => secondsToNanoseconds(value)).toThrow()
})
let app: ReturnType<typeof createApp> | undefined
afterEach(() => { app?.unmount(); document.body.innerHTML = ''; account.permissions = {}; vi.unstubAllGlobals() })
it('sends epoch-bound commands, permits pause during an advance and gates reset by capability', async () => {
  Object.assign(account, { actor: 'simulation-test', csrf: 'csrf', permissions: { 'simulation.control': ['*'] } })
  const execution = reactive<any>({ epoch: 'e1', state: 'paused', position_ns: '0', horizon_ns: '60000000000', capabilities: { reset: true } })
  const fetch = vi.fn(async () => new Response(JSON.stringify({ status: 'complete', result: { status: 'complete' } })))
  vi.stubGlobal('fetch', fetch)
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(SimulationPage, { execution, connected: true }) }); app.mount(root)
  const button = (name: string) => [...root.querySelectorAll('button')].find(b => b.textContent === name)!
  const input = [...root.querySelectorAll('label')].find(l => l.textContent?.includes('Target'))!.querySelector('input')!
  input.value = '0.000000001'; input.dispatchEvent(new Event('input')); await nextTick()
  button('Advance to target').click()
  for (let i = 0; i < 5; i++) await nextTick()
  const [path, request] = fetch.mock.calls[0] as unknown as [string, RequestInit]
  expect(path).toBe('/api/v1/simulation/commands')
  expect(JSON.parse(request.body as string)).toEqual({ epoch: 'e1', action: 'run_until', time_ns: '1' })
  expect(request.headers).toMatchObject({ 'X-CSRF-Token': 'csrf' })
  execution.state = 'running'; execution.active_operation = 'advance'; await nextTick()
  expect(button('Advance to target').disabled).toBe(true)
  expect(button('Pause simulation').disabled).toBe(false)
  execution.state = 'finished'; execution.active_operation = null; await nextTick()
  expect(button('Reset session to initial state').disabled).toBe(false)
  account.permissions = {}; await nextTick()
  expect(button('Reset session to initial state').disabled).toBe(true)
  expect(root.textContent).toContain('Observation only')
})

it('adds a time breakpoint with persistence across reset when selected', async () => {
  Object.assign(account, { actor: 'persistent-breakpoint-test', csrf: 'csrf', permissions: { 'simulation.control': ['*'] } })
  const fetch = vi.fn(async () => new Response(JSON.stringify({ status: 'complete', result: { status: 'complete' } })))
  vi.stubGlobal('fetch', fetch)
  const root = document.createElement('div'); document.body.append(root)
  app = createApp(SimulationPage, { execution: { epoch: 'e1', state: 'paused', capabilities: { persistent_breakpoints: true } }, connected: true }); app.mount(root)
  const checkbox = root.querySelector<HTMLInputElement>('input[type="checkbox"]')!
  checkbox.checked = true; checkbox.dispatchEvent(new Event('change')); await nextTick()
  ;[...root.querySelectorAll('button')].find(b => b.textContent === 'Add time breakpoint')!.click()
  for (let i = 0; i < 5; i++) await nextTick()
  const [, request] = fetch.mock.calls[0] as unknown as [string, RequestInit]
  expect(JSON.parse(request.body as string)).toEqual({ epoch: 'e1', action: 'break_add', time_ns: '1000000000', persistent: true })
})

it('offers compact epoch-bound actions and keeps pause available during an advance', async () => {
  Object.assign(account, { actor: 'simulation-actions-test', csrf: 'csrf', permissions: { 'simulation.control': ['*'] } })
  const execution = reactive<any>({ epoch: 'compact-epoch', state: 'paused', microsteps: false, capabilities: { reset: true } })
  const fetch = vi.fn(async () => new Response(JSON.stringify({ status: 'complete', result: { status: 'complete' } })))
  vi.stubGlobal('fetch', fetch)
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(SimulationActions, { execution, connected: true }) }); app.mount(root)
  const button = (name: string) => root.querySelector<HTMLButtonElement>(`button[aria-label="${name}"]`)!
  const toggle = button('Resume simulation')
  expect(toggle.textContent).toBe('▶')
  expect(button('Pause simulation')).toBeNull()
  toggle.click()
  for (let i = 0; i < 5; i++) await nextTick()
  expect(JSON.parse((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as string)).toEqual({ epoch: 'compact-epoch', action: 'resume' })
  await vi.waitFor(() => expect(button('Step one future instant').disabled).toBe(false))
  fetch.mockClear()
  expect(root.querySelector('input')).toBeNull()
  expect(button('Step one microstep')).toBeNull()
  button('Step one future instant').click()
  for (let i = 0; i < 5; i++) await nextTick()
  const [path, request] = fetch.mock.calls[0] as unknown as [string, RequestInit]
  expect(path).toBe('/api/v1/simulation/commands')
  expect(JSON.parse(request.body as string)).toEqual({ epoch: 'compact-epoch', action: 'step_instants', count: 1 })
  execution.state = 'running'; execution.active_operation = 'advance'; await nextTick()
  expect(button('Step one future instant').disabled).toBe(true)
  expect(button('Pause simulation').disabled).toBe(false)
  expect(button('Pause simulation')).toBe(toggle)
  expect(toggle.textContent).toBe('Ⅱ')
  expect(button('Resume simulation')).toBeNull()
  toggle.click()
  for (let i = 0; i < 5; i++) await nextTick()
  expect(JSON.parse((fetch.mock.calls[1] as unknown as [string, RequestInit])[1].body as string)).toEqual({ epoch: 'compact-epoch', action: 'pause' })
  expect(button('Stop simulation session').disabled).toBe(false)
  expect(button('Reset session to initial state').disabled).toBe(true)
  execution.state = 'finished'; execution.active_operation = null; await nextTick()
  expect(button('Resume simulation').disabled).toBe(true)
  expect(button('Stop simulation session').disabled).toBe(true)
  expect(button('Reset session to initial state').disabled).toBe(false)
  execution.microsteps = true; await nextTick()
  expect(button('Step one microstep')).not.toBeNull()
  account.permissions = {}; await nextTick()
  expect([...root.querySelectorAll('button')].every(button => button.disabled)).toBe(true)
})
