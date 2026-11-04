import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'
import ComponentsPage from '../src/components/ComponentsPage.vue'
import LinkContract from '../src/components/LinkContract.vue'

let app: ReturnType<typeof createApp> | undefined
afterEach(() => { app?.unmount(); document.body.innerHTML = ''; vi.unstubAllGlobals() })

function contract(root = 'Signal') {
  return { root, types: {
    [root]: { kind: 'message', fields: [
      { id: 1, name: 'samples', type: 'fl32', cardinality: 'repeated', dimensions: [16], wire: { q: '0.1' } },
      { id: 2, name: 'details', type: 'Details', cardinality: 'optional', dimensions: [], wire: {} },
    ] },
    Details: { kind: 'message', fields: [
      { id: 1, name: 'mode', type: 'Mode', cardinality: 'required', dimensions: [], wire: {} },
    ] },
    Mode: { kind: 'enum', values: [{ name: 'OFF', value: 0 }, { name: 'ON', value: 1 }] },
  } }
}
function mount(component: any, props: any) {
  const root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(component, props.value) }); app.mount(root)
  return root
}
async function until(predicate: () => boolean) {
  for (let n = 0; n < 100 && !predicate(); n++) {
    await new Promise(resolve => setTimeout(resolve, 5)); await nextTick()
  }
  expect(predicate()).toBe(true)
}

it('loads a link contract on selection and keeps it across ordinary status refreshes', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify(contract())))
  vi.stubGlobal('fetch', fetch)
  const report = { epoch: 'epoch-one', topology: { version: 1, components: {
    source: { outputs: { signal: { root: 'Signal' } } }, sink: {},
  }, subscriptions: { 'signal/#1': { from: 'source.signal', to: 'sink.input' } } }, components: [] }
  const props = ref({ report })
  const root = mount(ComponentsPage, props)
  expect(fetch).not.toHaveBeenCalled()
  root.querySelector('.flow-edge text')!.dispatchEvent(new Event('click', { bubbles: true }))
  await until(() => !!root.querySelector('.link-contract .schema-view'))
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0][0]).toBe('/api/v1/flow/schema?subscription=signal%2F%231&epoch=epoch-one')
  const view = root.querySelector('.link-contract .schema-view')!
  expect(view.textContent).toContain('samples')
  expect(view.textContent).toContain('fl32[16]')
  expect(view.textContent).toContain('repeated')
  expect(view.textContent).toContain('optional')
  expect(view.textContent).toContain('OFF = 0, ON = 1')
  expect(view.textContent).toContain('0.1')
  props.value = { report: { ...report } }
  await nextTick()
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(root.querySelector('.link-contract .schema-view')).toBeTruthy()
  props.value = { report: { ...report, epoch: 'epoch-two' } }
  await until(() => fetch.mock.calls.length === 2)
  expect(fetch.mock.calls[1][0]).toContain('epoch=epoch-two')
})

it('ignores an old response after selecting another link', async () => {
  const replies: Array<(response: Response) => void> = []
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => replies.push(resolve))))
  const props = ref({ subscription: 'first', epoch: 'epoch-one' })
  const root = mount(LinkContract, props)
  expect(root.textContent).toContain('Loading SDL contract')
  props.value = { subscription: 'second', epoch: 'epoch-one' }; await nextTick()
  expect(replies).toHaveLength(2)
  replies[1](new Response(JSON.stringify(contract('Second'))))
  await until(() => !!root.querySelector('.schema-view'))
  expect(root.querySelector('.schema-view>p')?.textContent).toContain('Second')
  replies[0](new Response(JSON.stringify(contract('First'))))
  await new Promise(resolve => setTimeout(resolve, 10)); await nextTick()
  expect(root.querySelector('.schema-view>p')?.textContent).toContain('Second')
})

it('shows a contract failure and lets the user retry without closing link details', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ error: { code: 'SOURCE_UNAVAILABLE', message: 'OSE unavailable.' } }), { status: 503 }))
  vi.stubGlobal('fetch', fetch)
  const root = mount(LinkContract, ref({ subscription: 'signal', epoch: 'epoch-one' }))
  await until(() => !!root.querySelector('[role="alert"]'))
  expect(root.textContent).toContain('OSE unavailable.')
  fetch.mockImplementationOnce(async () => new Response(JSON.stringify(contract())))
  ;(root.querySelector('button') as HTMLButtonElement).click()
  await until(() => !!root.querySelector('.schema-view'))
  expect(root.querySelector('[role="alert"]')).toBeNull()
  expect(fetch).toHaveBeenCalledTimes(2)
})
