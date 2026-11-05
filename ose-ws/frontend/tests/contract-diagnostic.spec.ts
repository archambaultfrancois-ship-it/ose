import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import ContractDiagnostic from '../src/components/ContractDiagnostic.vue'
import LinkContract from '../src/components/LinkContract.vue'

let app: ReturnType<typeof createApp> | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals() })

function report() {
  const field = { name: 'old', type: 'uint8', dimensions: [], cardinality: 'required', wire: {} }
  return { compatible: true, identical: false, source: { root: 'Packet', endpoint: 'source.output' },
    destination: { root: 'Packet', endpoint: 'sink.input' }, diagnostic: { code: 'COMPATIBLE', path: '' }, truncated: false,
    fields: [
      { path: 'Packet.old', destination_path: 'Packet.renamed', field_id: 1, status: 'different', source: field,
        destination: { ...field, name: 'renamed' }, findings: [{ code: 'FIELD_RENAMED', severity: 'info', message: 'Field names differ; matching uses the numeric field ID.' }] },
      { path: 'Packet.label', destination_path: 'Packet.label', field_id: 2, status: 'matching', source: { ...field, name: 'label' }, destination: { ...field, name: 'label' }, findings: [] },
    ] }
}
function mount(component: any = ContractDiagnostic) {
  const props = ref({ subscription: 'signal/#1', epoch: 'epoch-one' })
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(component, props.value) }); app.mount(root)
  return { root, props, button: () => root.querySelector('.contract-diagnostic button') as HTMLButtonElement }
}
async function until(predicate: () => boolean) {
  for (let n = 0; n < 100 && !predicate(); n++) { await new Promise(resolve => setTimeout(resolve, 5)); await nextTick() }
  expect(predicate()).toBe(true)
}

it('checks only on request, shows both endpoints and filters compatible differences', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify(report())))
  vi.stubGlobal('fetch', fetch)
  const { root, props, button } = mount()
  expect(fetch).not.toHaveBeenCalled()
  button().click()
  await until(() => !!root.querySelector('.diagnostic-result'))
  expect(fetch.mock.calls[0][0]).toBe('/api/v1/flow/diagnostic?subscription=signal%2F%231&epoch=epoch-one')
  expect(root.querySelector('.diagnostic-result')?.textContent).toContain('Compatible')
  expect(root.textContent).toContain('source.output')
  expect(root.textContent).toContain('sink.input')
  expect(root.textContent).toContain('Packet.renamed')
  expect(root.textContent).toContain('numeric field ID')
  expect(root.querySelectorAll('tbody tr')).toHaveLength(2)
  ;(root.querySelector('input') as HTMLInputElement).click(); await nextTick()
  expect(root.querySelectorAll('tbody tr')).toHaveLength(1)
  props.value = { ...props.value }; await nextTick()
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(root.querySelector('.diagnostic-result')).toBeTruthy()
})

it('shows the complete incompatibility verdict even when field details are truncated', async () => {
  const data = { ...report(), compatible: false, truncated: true,
    diagnostic: { code: 'REQUIRED_FIELD_MISSING', path: 'Packet.missing' } }
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(data))))
  const { root, button } = mount()
  button().click(); await until(() => !!root.querySelector('.diagnostic-result'))
  expect(root.querySelector('.diagnostic-result')?.textContent).toBe('Incompatible')
  expect(root.textContent).toContain('REQUIRED_FIELD_MISSING')
  expect(root.textContent).toContain('Packet.missing')
  expect(root.textContent).toContain('256 rows')
  expect(root.textContent).toContain('complete root contract')
})

it('explains root mismatches without showing a misleading field table', async () => {
  const data = { ...report(), compatible: false, fields: [], diagnostic: { code: 'ROOT_MISMATCH', path: 'Packet' },
    destination: { root: 'Other', endpoint: 'sink.input' } }
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(data))))
  const { root, button } = mount()
  button().click(); await until(() => !!root.querySelector('.diagnostic-result'))
  expect(root.textContent).toContain('root types differ')
  expect(root.textContent).toContain('Other')
  expect(root.querySelector('table')).toBeNull()
})

it('discards replies for a different link or application epoch', async () => {
  const replies: Array<(response: Response) => void> = []
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => replies.push(resolve))))
  const { root, props, button } = mount()
  button().click()
  props.value = { subscription: 'other', epoch: 'epoch-two' }; await nextTick()
  expect(root.querySelector('.diagnostic-result')).toBeNull()
  button().click()
  replies[1](new Response(JSON.stringify({ ...report(), source: { endpoint: 'new.output', root: 'New' } })))
  await until(() => !!root.querySelector('.diagnostic-result'))
  replies[0](new Response(JSON.stringify(report())))
  await new Promise(resolve => setTimeout(resolve, 10)); await nextTick()
  expect(root.textContent).toContain('new.output')
  expect(root.textContent).not.toContain('source.output')
  props.value.epoch = 'epoch-three'; await nextTick()
  expect(root.querySelector('.diagnostic-result')).toBeNull()
})

it('keeps the source schema available and retries an unsupported or failed diagnostic', async () => {
  const schema = { root: 'Packet', types: { Packet: { kind: 'message', fields: [] } } }
  const fetch = vi.fn(async (path: string) => new Response(JSON.stringify(path.includes('/flow/schema') ? schema
    : { error: { code: 'CONTRACT_DIAGNOSTIC_UNSUPPORTED', message: 'Restart OSE to check both contracts.' } }),
    { status: path.includes('/flow/schema') ? 200 : 503 }))
  vi.stubGlobal('fetch', fetch)
  const { root, button } = mount(LinkContract)
  await until(() => !!root.querySelector('.schema-view'))
  expect(fetch).toHaveBeenCalledTimes(1)
  button().click(); await until(() => !!root.querySelector('.contract-diagnostic [role="alert"]'))
  expect(root.querySelector('.schema-view')).toBeTruthy()
  expect(root.textContent).toContain('Restart OSE')
  fetch.mockImplementationOnce(async () => new Response(JSON.stringify(report())))
  button().click(); await until(() => !!root.querySelector('.diagnostic-result'))
  expect(root.querySelector('.contract-diagnostic [role="alert"]')).toBeNull()
  expect(fetch).toHaveBeenCalledTimes(3)
})
