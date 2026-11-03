import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'
import LogsPage from '../src/components/LogsPage.vue'

let app: ReturnType<typeof createApp> | undefined
afterEach(() => { app?.unmount(); document.body.innerHTML = ''; vi.unstubAllGlobals(); vi.restoreAllMocks() })
async function until(predicate: () => boolean) {
  for (let n = 0; n < 150 && !predicate(); n++) { await new Promise(resolve => setTimeout(resolve, 5)); await nextTick() }
  expect(predicate()).toBe(true)
}
function mount(props = { active: true, connected: true, allowed: true }) {
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(LogsPage, props) }); app.mount(root)
  return root
}
function button(root: Element, label: string) {
  return [...root.querySelectorAll('button')].find(node => node.textContent === label) as HTMLButtonElement
}
function record(sequence: string, message = 'Failure <script>untrusted</script>', level = 'FATAL') {
  return { sequence, message, level, logger: 'detection', component: 'sensor_1', generation: 'one', time_ns: '1730000000000000000' }
}

it.each([['fr-FR', '00:02:03,127'], ['en-US', '00:02:03.127']])(
  'displays hours, minutes, seconds and milliseconds in %s', async (locale, expected) => {
    const format = Date.prototype.toLocaleTimeString
    vi.spyOn(Date.prototype, 'toLocaleTimeString').mockImplementation(function (this: Date, _locales, options) {
      return format.call(this, locale, options)
    })
    const stamp = String(BigInt(new Date(2026, 10, 3, 0, 2, 3, 127).getTime()) * 1000000n + 987654n)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ epoch: 'run',
      items: [{ ...record('1'), time_ns: stamp }], cursor: '1', more: false, truncated: false, retained: 1,
    }), { status: 200 })))
    const root = mount()
    await until(() => !!root.querySelector('tbody td.mono'))
    const cell = root.querySelector('tbody td.mono')!
    expect(cell.textContent).toBe(expected)
    expect(cell.getAttribute('title')).toBe(stamp + ' ns UTC')
  },
)

it('filters FATAL logs, escapes messages, and resumes from the last displayed cursor', async () => {
  const requests: URLSearchParams[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const query = new URL(url, 'http://localhost').searchParams
    requests.push(query)
    return new Response(JSON.stringify({ epoch: 'run', items: [record(String(requests.length))], cursor: String(requests.length),
      more: false, truncated: false, retained: requests.length }), { status: 200 })
  }))
  const root = mount()
  await until(() => root.textContent?.includes('Failure <script>untrusted</script>') ?? false)
  expect(root.querySelector('script')).toBeNull()
  const select = root.querySelector('select')!
  select.value = 'FATAL'; select.dispatchEvent(new Event('change', { bubbles: true }))
  await until(() => requests.length === 2)
  expect(requests[1].get('levels')).toBe('FATAL')
  expect(requests[1].has('after')).toBe(false)
  button(root, 'Pause logs').click(); await nextTick()
  expect(root.textContent).toContain('Display paused; runtime collection continues.')
  button(root, 'Resume logs').click()
  await until(() => requests.length === 3)
  expect(requests[2].get('after')).toBe('2')
  expect(requests[2].get('epoch')).toBe('run')
  expect(root.querySelector('.log-level[data-level="FATAL"]')).toBeTruthy()
})

it('keeps the view after a transport failure and reports a restarted or truncated stream', async () => {
  let calls = 0
  vi.stubGlobal('fetch', vi.fn(async () => {
    calls++
    if (calls === 2) throw new TypeError('Connection lost')
    return new Response(JSON.stringify({ epoch: calls === 1 ? 'old' : 'new', items: [record('1', calls === 1 ? 'Earlier entry' : 'New runtime')],
      cursor: '1', more: false, truncated: calls > 2, retained: 1 }), { status: 200 })
  }))
  const root = mount()
  await until(() => !!root.textContent?.includes('Earlier entry'))
  button(root, 'Refresh logs').click()
  await until(() => !!root.querySelector('[role="alert"]'))
  expect(root.textContent).toContain('Earlier entry')
  button(root, 'Refresh logs').click()
  await until(() => !!root.textContent?.includes('New runtime'))
  expect(root.textContent).not.toContain('Earlier entry')
  expect(root.textContent).toContain('Some entries are unavailable.')
})

it('does not read logs while the page is hidden or the user lacks access', async () => {
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  const props = reactive({ active: false, connected: true, allowed: true })
  const root = mount(props)
  await nextTick(); expect(fetch).not.toHaveBeenCalled()
  props.active = true; props.allowed = false
  await nextTick(); expect(fetch).not.toHaveBeenCalled()
  expect(root.textContent).toContain('do not have permission')
})
