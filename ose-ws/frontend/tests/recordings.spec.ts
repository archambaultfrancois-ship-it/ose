import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import RecordingsPage from '../src/components/RecordingsPage.vue'
import { account } from '../src/api'

let app: ReturnType<typeof createApp> | undefined
afterEach(() => {
  app?.unmount()
  document.body.innerHTML = ''
  account.actor = ''
  vi.unstubAllGlobals()
})

function capture(id = 'capture', state = 'STOPPED', allowed = true) {
  return { id, state, owner: 'recordings-test', permissions: { delete: allowed }, settings: { label: id, format: 'pcapng' }, selection: [] }
}
function mount(captures: any[], refresh = () => {}) {
  const root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(RecordingsPage, { points: [], captures, onRefresh: refresh }) })
  app.mount(root)
  return root
}
function button(root: Element, label: string) {
  const element = [...root.querySelectorAll('button')].find(node => node.textContent === label)
  if (!element) throw new Error(`Button ${label} not found`)
  return element as HTMLButtonElement
}
async function until(predicate: () => boolean) {
  for (let n = 0; n < 100 && !predicate(); n++) {
    await new Promise(resolve => setTimeout(resolve, 5))
    await nextTick()
  }
  expect(predicate()).toBe(true)
}

it.each([false, true])('confirms capture removal with delete_files=%s and refreshes the list', async deleteFiles => {
  account.actor = 'recordings-test'
  const captures = ref([capture()])
  const requests: any[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, options: any) => {
    requests.push({ path, method: options.method, data: JSON.parse(options.body) })
    return new Response(JSON.stringify({ id: 'operation', status: 'complete', result: {
      recording_id: 'capture', deleted: true, files_deleted: deleteFiles, retained_directory: deleteFiles ? null : 'captures/capture/',
    } }), { status: 200 })
  }))
  const root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(RecordingsPage, { points: [], captures: captures.value, onRefresh: () => { captures.value = [] } }) })
  app.mount(root)
  button(root, 'Delete').click()
  await nextTick()
  expect(root.querySelector('[role="dialog"]')).toBeTruthy()
  expect(requests).toEqual([])
  button(root, 'Cancel').click()
  await nextTick()
  expect(root.querySelector('[role="dialog"]')).toBeNull()
  expect(requests).toEqual([])
  button(root, 'Delete').click()
  await nextTick()
  const select = root.querySelector('[role="dialog"] select') as HTMLSelectElement
  expect(select.selectedIndex).toBe(0)
  if (deleteFiles) {
    select.selectedIndex = 1
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
  }
  button(root, deleteFiles ? 'Delete capture and files' : 'Delete capture').click()
  await until(() => !!root.textContent?.includes('No captures yet.'))
  expect(requests).toEqual([{ path: '/api/v1/recordings/capture', method: 'DELETE', data: { delete_files: deleteFiles } }])
  expect(root.textContent).toContain(deleteFiles ? 'Capture and its files deleted.' : 'captures/capture/')
})

it('hides deletion for active or unauthorized captures and retains a refused deletion', async () => {
  account.actor = 'recordings-test'
  const refresh = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ id: 'refused', status: 'failed',
    result: { code: 'CAPTURE_ACTIVE', error: 'Stop and finalize the capture before deleting it.' },
  }), { status: 200 })))
  const root = mount([capture('active', 'RECORDING'), capture('read-only', 'STOPPED', false), capture('draft', 'DRAFT')], refresh)
  expect([...root.querySelectorAll('button')].filter(node => node.textContent === 'Delete')).toHaveLength(1)
  button(root, 'Delete').click()
  await nextTick()
  button(root, 'Delete capture').click()
  await until(() => !!root.querySelector('[role="alert"]'))
  expect(root.textContent).toContain('Stop and finalize the capture before deleting it.')
  expect(root.querySelector('[role="dialog"]')).toBeTruthy()
  expect(root.querySelectorAll('.capture')).toHaveLength(3)
  expect(refresh).not.toHaveBeenCalled()
})
