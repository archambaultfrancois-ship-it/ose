import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import RecordingsPage from '../src/components/RecordingsPage.vue'
import { account } from '../src/api'

let app: ReturnType<typeof createApp> | undefined

it('selects and filters same-named inputs and outputs independently by point identity', async () => {
  const points = [
    { id: 'point-in', topic: 'node.value', kind: 'input', root: 'Value', available: true, sources: ['a.out', 'b.out'], reception: { buffer: 'fifo', capacity: 8 } },
    { id: 'point-out', topic: 'node.value', kind: 'output', root: 'Value', available: true },
  ]
  const requests: any[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, options: any) => {
    requests.push({ path, data: JSON.parse(options.body) })
    return new Response(JSON.stringify({ status: 'complete', result: { selection: points } }), { status: 200 })
  }))
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(RecordingsPage, { points, captures: [] }) }); app.mount(root)
  const kind = root.querySelector('select') as HTMLSelectElement
  kind.value = 'input'; kind.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
  expect(root.querySelectorAll('.point')).toHaveLength(1)
  expect(root.textContent).toContain('Sources: a.out, b.out')
  ;(root.querySelector('.point input[type="checkbox"]') as HTMLInputElement).click(); await nextTick()
  const inputFilter = root.querySelector('.filter-row input') as HTMLInputElement
  inputFilter.value = 'value > 10'; inputFilter.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
  button(root, 'Validate').click(); await until(() => requests.length === 1)
  expect(requests[0].data).toEqual({ point_id: 'point-in', expression: 'value > 10' })
  kind.value = ''; kind.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
  const checkboxes = root.querySelectorAll<HTMLInputElement>('.point input[type="checkbox"]')
  expect(checkboxes[0].checked).toBe(true); expect(checkboxes[1].checked).toBe(false)
  checkboxes[1].click(); await nextTick()
  const outputFilter = root.querySelectorAll<HTMLInputElement>('.filter-row input')[1]
  expect(outputFilter.value).toBe('')
  outputFilter.value = 'value < 5'; outputFilter.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
  button(root, 'Create capture draft').click(); await until(() => requests.length === 2)
  expect(requests[1].data.selection).toEqual(['point-in', 'point-out'])
  expect(requests[1].data.filters).toEqual({ 'point-in': 'value > 10', 'point-out': 'value < 5' })
})

it('keeps same-named point counters and duplicated configurations separate', async () => {
  const selection = [
    { topic: 'node.value', kind: 'input', point_id: 'point-in', point_key: 'input:node.value', root: 'Value', filter: 'value > 10' },
    { topic: 'node.value', kind: 'output', point_id: 'point-out', point_key: 'node.value', root: 'Value', filter: null },
  ]
  const points = selection.map(p => ({ ...p, id: p.point_id, available: true }))
  const recorded = { ...capture(), selection, status: { topics: { 'input:node.value': 3, 'node.value': 7 }, topic_bytes: { 'input:node.value': 1000, 'node.value': 7000 } } }
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(RecordingsPage, { points, captures: [recorded] }) }); app.mount(root)
  const rows = root.querySelectorAll('.point-selection li')
  expect(rows[0].textContent).toContain('input'); expect(rows[0].textContent).toContain('3 recorded')
  expect(rows[1].textContent).toContain('output'); expect(rows[1].textContent).toContain('7 recorded')
  expect(rows[0].querySelector('.point-recording-size')?.textContent).toContain('1.00 KB')
  expect(rows[1].querySelector('.point-recording-size')?.textContent).toContain('7.00 KB')
  button(root, 'Duplicate configuration').click(); await nextTick()
  expect([...root.querySelectorAll<HTMLInputElement>('.point input[type="checkbox"]')].map(p => p.checked)).toEqual([true, true])
  expect([...root.querySelectorAll<HTMLInputElement>('.filter-row input')].map(p => p.value)).toEqual(['value > 10', ''])
})
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

it('updates current file size from live snapshots, including rotation and unavailable sizes', async () => {
  const captures = ref([{ ...capture('live', 'RECORDING'), status: { file_size_bytes: 12345 } }])
  const root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(RecordingsPage, { points: [], captures: captures.value }) })
  app.mount(root)
  expect(root.querySelector('.capture-file-size')?.textContent).toContain('12.3 KB')
  captures.value[0].status.file_size_bytes = 12345678
  await nextTick()
  expect(root.querySelector('.capture-file-size')?.textContent).toContain('12.3 MB')
  captures.value[0].status.file_size_bytes = 0 // A new file after rotation.
  await nextTick()
  expect(root.querySelector('.capture-file-size')?.textContent).toContain('0.00 KB')
  captures.value = [{ ...capture('live', 'STOPPED'), status: { file_size_bytes: null as any } }]
  await nextTick()
  expect(root.querySelector('.capture-file-size')?.textContent).toContain('—')
})

it('shows independent point counts and data sizes as live capture snapshots change', async () => {
  const selection = [
    { topic: 'source.first', root: 'Value' },
    { topic: 'source.second', root: 'Value', filter: 'value > 0' },
  ]
  const captures = ref<any[]>([{ ...capture('live', 'DRAFT'), selection }])
  const root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(RecordingsPage, { points: [], captures: captures.value }) })
  app.mount(root)
  const rows = () => root.querySelectorAll('.point-selection li')
  expect(rows()).toHaveLength(2)
  expect(rows()[0].querySelector('.point-recording-count')?.textContent).toContain('0 recorded')
  expect(rows()[0].querySelector('.point-recording-size')?.textContent).toContain('0.00 KB')
  captures.value = [{ ...capture('live', 'RECORDING'), selection, status: {
    topics: { 'source.first': 40, 'source.second': 7 },
    topic_bytes: { 'source.first': 12345, 'source.second': 12345678 },
  } }]
  await nextTick()
  expect(rows()[0].querySelector('.point-recording-count')?.textContent).toContain('40 recorded')
  expect(rows()[0].querySelector('.point-recording-size')?.textContent).toContain('12.3 KB')
  expect(rows()[1].querySelector('.point-recording-count')?.textContent).toContain('7 recorded')
  expect(rows()[1].querySelector('.point-recording-size')?.textContent).toContain('12.3 MB')
  expect(rows()[1].textContent).toContain('value > 0')
  captures.value[0].status.topics['source.first'] = 81
  captures.value[0].status.topic_bytes['source.first'] = 1234567890
  await nextTick()
  expect(rows()[0].querySelector('.point-recording-count')?.textContent).toContain('81 recorded')
  expect(rows()[0].querySelector('.point-recording-size')?.textContent).toContain('1.23 GB')
  // Captures made by older engines have no byte statistics.
  captures.value = [{ ...capture('legacy', 'STOPPED'), selection, status: { topics: { 'source.first': 2 } } }]
  await nextTick()
  expect(rows()[0].querySelector('.point-recording-size')?.textContent).toContain('—')
})

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
