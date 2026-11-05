import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive, ref } from 'vue'
import SchemaField from '../src/components/SchemaField.vue'
import ParametersPage from '../src/components/ParametersPage.vue'
import ComponentsPage from '../src/components/ComponentsPage.vue'

const cleanups: (() => void)[] = []
afterEach(() => { cleanups.splice(0).forEach(fn => fn()); document.body.innerHTML = ''; vi.unstubAllGlobals() })
const schema = { root: 'Settings', schema_digest: 'digest', types: { Settings: { kind: 'message', fields: [
  { id: 1, name: 'counter', type: 'uint64', cardinality: 'required', dimensions: [] },
  { id: 2, name: 'label', type: 'string', cardinality: 'optional', dimensions: [] },
] } }, presentation: {} }
function mount(render: () => any) {
  const element = document.createElement('div'); document.body.append(element)
  const app = createApp({ render }); app.mount(element); cleanups.push(() => app.unmount())
  return element
}
function button(element: Element, text: string) {
  const found = [...element.querySelectorAll('button')].find(node => node.textContent?.includes(text))
  if (!found) throw new Error(`Button ${text} not found`)
  return found as HTMLButtonElement
}
async function waitFor(predicate: () => boolean) {
  for (let n = 0; n < 100 && !predicate(); n++) { await new Promise(resolve => setTimeout(resolve, 5)); await nextTick() }
  expect(predicate()).toBe(true)
}
describe('typed SDL interactions', () => {
  it('edits the maximum uint64 exactly and controls optional presence', async () => {
    const value = ref({ counter: '18446744073709551615', label: null as null | string })
    const element = mount(() => h(SchemaField, { schema, type: 'Settings', modelValue: value.value, label: 'Value',
      'onUpdate:modelValue': (updated: any) => { value.value = updated } }))
    const input = element.querySelector('[aria-label="counter"]') as HTMLInputElement
    expect(input.value).toBe('18446744073709551615')
    input.value = '9223372036854775808'; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    expect(value.value.counter).toBe('9223372036854775808')
    const presence = element.querySelector('input[type="checkbox"]') as HTMLInputElement
    presence.checked = true; presence.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
    expect(value.value.label).toBe('')
    presence.checked = false; presence.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
    expect(value.value.label).toBe(null)
  })

  it('preserves a reactive draft on remote changes and applies only its validated preview', async () => {
    const remote = ref({ counter: '18446744073709551615', label: null })
    const properties = reactive({ blocks: [{ id: 'block', path: 'machine/settings', permissions: { read: true, write: true } }], change: null as any, connected: true })
    const requests: { path: string; method: string; data: any }[] = []
    vi.stubGlobal('fetch', vi.fn(async (path: string, options: any = {}) => {
      const data = options.body ? JSON.parse(options.body) : null
      requests.push({ path, method: options.method || 'GET', data })
      let result: any
      if (path.endsWith('/schema')) result = schema
      else if (path.endsWith('/validate')) result = { value: data.value, schema_digest: 'digest', preview_hash: 'preview', encoded_bytes: 10, normalized: false }
      else if (options.method === 'PUT') { remote.value = data.value; result = { id: 'operation', status: 'complete', result: { result: 'stored' } } }
      else result = { value: { ...remote.value }, schema_digest: 'digest', observed_at: '2026-11-02T12:00:00Z' }
      return new Response(JSON.stringify(result), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const element = mount(() => h(ParametersPage, properties))
    button(element, 'settings').click()
    await waitFor(() => !!element.querySelector('[aria-label="counter"]'))
    const input = element.querySelector('[aria-label="counter"]') as HTMLInputElement
    input.value = '9223372036854775808'; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    expect(button(element, 'Apply').disabled).toBe(false)
    expect([...element.querySelectorAll('button')].some(node => node.textContent?.includes('Validate'))).toBe(false)
    remote.value = { counter: '7', label: null }; properties.change = { block_id: 'block', sequence: '2' }
    await waitFor(() => !!element.textContent?.includes('The remote value changed.'))
    expect(input.value).toBe('9223372036854775808')
    button(element, 'Apply').click()
    await waitFor(() => requests.some(request => request.method === 'PUT'))
    expect(requests.find(request => request.method === 'PUT')?.data.value.counter).toBe('9223372036854775808')
    expect(requests.findIndex(request => request.path.endsWith('/validate'))).toBeLessThan(requests.findIndex(request => request.method === 'PUT'))
    await waitFor(() => !!element.textContent?.includes('Stored by OSE.'))
    input.value = '8'; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    button(element, 'Discard changes').click(); await nextTick()
    expect(input.value).toBe('9223372036854775808')
  })

  it('waits for validation, ignores duplicate clicks and writes the server-normalized value', async () => {
    let finishValidation!: () => void
    const pendingValidation = new Promise<void>(resolve => { finishValidation = resolve })
    const requests: { path: string; method: string; data: any }[] = []
    const normalized = { threshold: 1.2345678806304932 }
    vi.stubGlobal('fetch', vi.fn(async (path: string, options: any = {}) => {
      requests.push({ path, method: options.method || 'GET', data: options.body ? JSON.parse(options.body) : null })
      if (path.endsWith('/schema')) return new Response(JSON.stringify({ root: 'Settings', schema_digest: 'digest', presentation: {}, types: {
        Settings: { kind: 'message', fields: [{ id: 1, name: 'threshold', type: 'fl32', cardinality: 'required', dimensions: [] }] },
      } }))
      if (path.endsWith('/validate')) {
        await pendingValidation
        return new Response(JSON.stringify({ value: normalized, schema_digest: 'digest', preview_hash: 'normalized-preview', encoded_bytes: 4, normalized: true }))
      }
      if (options.method === 'PUT') return new Response(JSON.stringify({ id: 'normalized-write', status: 'complete', result: { result: 'stored' } }))
      return new Response(JSON.stringify({ value: { threshold: 0 }, schema_digest: 'digest' }))
    }))
    const element = mount(() => h(ParametersPage, { blocks: [{ id: 'block', path: 'machine/settings', permissions: { read: true, write: true } }], change: null, connected: true }))
    button(element, 'settings').click()
    await waitFor(() => !!element.querySelector('[aria-label="threshold"]'))
    const input = element.querySelector('[aria-label="threshold"]') as HTMLInputElement
    input.value = '1.23456789'; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    button(element, 'Apply').click()
    await waitFor(() => requests.some(request => request.path.endsWith('/validate')))
    expect(button(element, 'Apply').disabled).toBe(true)
    // Dispatch directly to exercise the handler guard even while the button is disabled.
    button(element, 'Apply').dispatchEvent(new Event('click', { bubbles: true }))
    expect(requests.filter(request => request.path.endsWith('/validate'))).toHaveLength(1)
    expect(requests.some(request => request.method === 'PUT')).toBe(false)
    finishValidation()
    await waitFor(() => !!element.textContent?.includes('Stored by OSE.'))
    expect(requests.filter(request => request.method === 'PUT')).toHaveLength(1)
    expect(requests.find(request => request.method === 'PUT')?.data).toMatchObject({ value: normalized, preview_hash: 'normalized-preview' })
  })

  it('freezes a reactive diagnostic view without mutating incoming reports', async () => {
    const props = reactive({ report: { components: [{ name: 'source', state: 'ACTIVE', severity: 'ok', generation: 'one',
      rates: { in: 1, out: 2, ticks: 3 }, queues: [], tick_p95_ms: 4, report_age_ms: 5 }] } })
    const element = mount(() => h(ComponentsPage, props))
    button(element, 'Freeze view').click(); await nextTick()
    props.report.components[0].name = 'replacement'; await nextTick()
    expect(element.textContent).toContain('source')
    expect(element.textContent).not.toContain('replacement')
    button(element, 'Resume view').click(); await nextTick()
    expect(element.textContent).toContain('replacement')
  })
})
