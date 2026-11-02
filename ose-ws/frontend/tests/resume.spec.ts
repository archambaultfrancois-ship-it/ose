import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import App from '../src/App.vue'
import { account } from '../src/api'

vi.mock('../src/components/ComponentsPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../src/components/MeasurementsPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../src/components/RecordingsPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../src/components/ParametersPage.vue', () => ({ default: { template: '<div />' } }))

let app: ReturnType<typeof createApp> | undefined
let root: HTMLDivElement

beforeEach(() => {
  window.history.replaceState(null, '', '/')
  account.actor = ''
  vi.stubGlobal('WebSocket', class {
    static OPEN = 1
    static CONNECTING = 0
    readyState = 0
    close() { this.readyState = 3 }
  })
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    const data = path.endsWith('/session') ? { actor: 'navigation-test', csrf: 'csrf', permissions: {}, expires_at: '' }
      : path.endsWith('/application') ? { connected: true }
      : path.endsWith('/components') ? { components: [] } : { items: [], next: null }
    return new Response(JSON.stringify(data), { status: 200 })
  }))
})
afterEach(() => {
  app?.unmount(); app = undefined
  account.actor = ''
  document.body.innerHTML = ''
  window.history.replaceState(null, '', '/')
  vi.unstubAllGlobals()
})

async function until(predicate: () => boolean) {
  for (let n = 0; n < 100 && !predicate(); n++) {
    await new Promise(resolve => setTimeout(resolve, 5))
    await nextTick()
  }
  expect(predicate()).toBe(true)
}
async function mount() {
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(App)
  app.mount(root)
  await until(() => !!root.querySelector('.page-heading h1'))
}
function heading() { return root.querySelector('.page-heading h1')?.textContent }

it.each(['measurements', 'recordings', 'parameters'])('restores %s after the browser reloads the tab', async page => {
  await mount()
  const label = page[0].toUpperCase() + page.slice(1)
  const button = [...root.querySelectorAll('nav button')].find(node => node.textContent?.includes(label)) as HTMLButtonElement
  button.click()
  await until(() => window.location.hash === '#' + page)
  expect(heading()).toBe(label)
  app!.unmount(); app = undefined
  root.remove()
  vi.mocked(fetch).mockClear()
  await mount()
  expect(heading()).toBe(label)
  expect(root.querySelector('nav button.active')?.textContent).toContain(label)
  await until(() => vi.mocked(fetch).mock.calls.some(([path]) => String(path).startsWith('/api/v1/' + (page === 'recordings' ? 'recording-points' : page))))
  expect(vi.mocked(fetch).mock.calls.some(([path]) => path === '/api/v1/components')).toBe(false)
})

it('uses Components for an absent or unknown section and follows fragment navigation', async () => {
  window.history.replaceState(null, '', '#unknown')
  await mount()
  expect(heading()).toBe('Components')
  window.history.replaceState(null, '', '#parameters')
  window.dispatchEvent(new HashChangeEvent('hashchange'))
  await until(() => heading() === 'Parameters')
  window.history.replaceState(null, '', '/')
  window.dispatchEvent(new HashChangeEvent('hashchange'))
  await until(() => heading() === 'Components')
})
