import { afterEach, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import App from '../src/App.vue'
import { account } from '../src/api'

let app: ReturnType<typeof createApp> | undefined
afterEach(() => {
  app?.unmount()
  document.body.innerHTML = ''
  account.actor = ''
  vi.unstubAllGlobals()
})

async function until(predicate: () => boolean) {
  for (let n = 0; n < 100 && !predicate(); n++) {
    await new Promise(resolve => setTimeout(resolve, 5))
    await nextTick()
  }
  expect(predicate()).toBe(true)
}

function visible(element: Element) {
  for (let node: Element | null = element; node; node = node.parentElement) {
    if (getComputedStyle(node).display === 'none') return false
  }
  return true
}

it('shows only the selected page and preserves parameter drafts across navigation', async () => {
  const session = { actor: 'navigation-test', csrf: 'csrf', permissions: { admin: ['*'] }, expires_at: '' }
  Object.assign(account, session)
  vi.stubGlobal('WebSocket', class {
    static OPEN = 1
    static CONNECTING = 0
    readyState = 0
    close() { this.readyState = 3 }
  })
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    let data: any = { items: [], next: null }
    if (path.endsWith('/session')) data = session
    else if (path.endsWith('/application')) data = { name: 'Test application', connected: true }
    else if (path.endsWith('/components')) data = { components: [] }
    else if (path.startsWith('/api/v1/parameters?')) data = { items: [
      { id: 'settings', path: 'machine/settings', permissions: { read: true, write: true } },
    ], next: null }
    else if (path.endsWith('/schema')) data = { root: 'Settings', schema_digest: 'digest', presentation: {}, types: {
      Settings: { kind: 'message', fields: [{ id: 1, name: 'counter', type: 'uint64', cardinality: 'required', dimensions: [] }] },
    } }
    else if (path.endsWith('/parameters/settings')) data = { value: { counter: '1' }, schema_digest: 'digest' }
    return new Response(JSON.stringify(data), { status: 200 })
  }))
  const root = document.createElement('div')
  document.body.append(root)
  app = createApp(App)
  app.mount(root)
  await until(() => root.textContent?.includes('Test application') ?? false)

  const pages = [
    ['Components', 'Search components'],
    ['Logs', 'Minimum level'],
    ['Measurements', 'Measurement catalogue'],
    ['Recordings', 'Recording points'],
    ['Parameters', 'Parameter blocks'],
  ]
  expect([...root.querySelectorAll('nav button')][1].textContent).toContain('Logs')
  function checkPage(label: string) {
    expect(root.querySelector('.page-heading h1')?.textContent).toBe(label)
    for (const [name, marker] of pages) {
      const content = [...root.querySelectorAll('label, h2')].find(node => node.textContent?.includes(marker))!
      expect(content, `${name} content exists`).toBeTruthy()
      expect(visible(content), `${name} visibility while ${label} is selected`).toBe(name === label)
    }
    for (const heading of root.querySelectorAll('h2')) {
      if (heading.textContent === 'Captures' || heading.textContent === 'Inspect a parameter block') {
        expect(visible(heading)).toBe(label === (heading.textContent === 'Captures' ? 'Recordings' : 'Parameters'))
      }
    }
  }
  async function select(label: string) {
    const button = [...root.querySelectorAll('nav button')].find(node => node.textContent?.includes(label)) as HTMLButtonElement
    button.click()
    await nextTick()
    checkPage(label)
  }
  checkPage('Components')
  for (const [label] of pages.slice(1)) await select(label)

  await until(() => !!root.querySelector('.block-list button'))
  ;(root.querySelector('.block-list button') as HTMLButtonElement).click()
  await until(() => !!root.querySelector('[aria-label="counter"]'))
  const input = root.querySelector('[aria-label="counter"]') as HTMLInputElement
  input.value = '18446744073709551615'
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  for (const [label] of pages.slice(0, 3)) await select(label)
  await select('Parameters')
  expect((root.querySelector('[aria-label="counter"]') as HTMLInputElement).value).toBe('18446744073709551615')
})
