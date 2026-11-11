// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { navigationIndex, searchNavigation } from '../src/navigation'
import { currentDiagnostics, guideDiagnostic } from '../src/diagnostics'
import { sourcePosition } from '../src/source-location'
import { useProject } from '../src/store'
import App from '../src/App.vue'
import SourceEditor from '../src/components/SourceEditor.vue'
import DiagnosticsView from '../src/components/DiagnosticsView.vue'
import NavigationPalette from '../src/components/NavigationPalette.vue'

function project(): any {
  return { name: 'Example', revision: 'r1', model: 'model.json', deployments: [], diagnostics: [], workspace: { positions: {}, groups: {}, pinned: [] }, documents: {
    'model.json': { id: 'model.json', role: 'model', format: 'json', tree: { path: [], children: [] }, value: { version: 2, schemas: 'types.sdl', components: {
      source: { inputs: {}, outputs: { signal: { root: 'Reading' } } },
      compute: { inputs: { signal: { root: 'Reading', mode: 'pull', timeout: .5, activates: { message: ['out'] } } }, outputs: { out: { root: 'Reading' } } },
    }, subscriptions: [{ id: 'reading-stream', from: 'source.signal', to: 'compute.signal', qos: { buffer: 'latest', capacity: 9, overflow: 'reject_new', ack: 'none', retry: { max_retries: 3 } } }] } },
    'types.sdl': { id: 'types.sdl', role: 'schema', format: 'sdl', nodes: [{ name: 'Reading', kind: 'message', path: '/0', start: 0, children: [{ name: 'value', kind: 'field', path: '/0/0', start: 18, children: [] }] }] },
    'types.json': { id: 'types.json', role: 'schema', format: 'json', tree: { path: [], children: [{ name: 'État', path: ['messages', 'État'], children: [] }] } },
  } }
}
let wrapper: VueWrapper | undefined
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.innerHTML = '' })

it('indexes ports and subscriptions by identity, nested SDL fields and descriptor declarations', () => {
  const state = project(), index = navigationIndex(state)
  expect(searchNavigation(index, 'compute.signal')[0]).toMatchObject({ path: ['components', 'compute', 'inputs', 'signal'], kind: 'Input' })
  expect(searchNavigation(index, 'reading-stream')[0].path).toEqual(['subscriptions', '0'])
  expect(searchNavigation(index, 'Reading.value')[0]).toMatchObject({ file: 'types.sdl', sdl: '/0/0' })
  expect(searchNavigation(index, 'etat types.json')[0].file).toBe('types.json')
  state.documents['model.json'].value.subscriptions = [null, { id: 'valid' }]
  expect(() => navigationIndex(state)).not.toThrow()
})

it('keeps validation diagnostics after reopening and excludes obsolete jobs', () => {
  const state = project(), diagnostic = { message: 'latest requires capacity=1 and drop_oldest' }
  state.validation = { revision: 'r1', diagnostics: [diagnostic] }
  expect(currentDiagnostics(state, null)).toEqual([diagnostic])
  expect(currentDiagnostics(state, { revision: 'old', diagnostics: [{ message: 'old' }] })).toEqual([diagnostic])
  state.revision = 'r2'
  expect(currentDiagnostics(state, null)).toEqual([])
})

describe('diagnostic guidance', () => {
  it('locates a broken endpoint at the actual subscription field, rather than a nonexistent port', () => {
    const state = project(); state.documents['model.json'].value.subscriptions[0].to = 'compute.missing'
    const guide = guideDiagnostic(state, { file: 'model.json', message: 'unknown inputs endpoint: compute.missing' })
    expect(guide.targets[0].path).toEqual(['subscriptions', '0', 'to'])
    expect(guide.fixes).toEqual([])
  })
  it('offers explicit preview commands for latest, Pull and retry repairs', () => {
    const state = project()
    const latest = guideDiagnostic(state, { file: 'model.json', message: 'latest requires capacity=1 and drop_oldest' })
    expect(latest.fixes[0].command).toEqual({ action: 'set_reception', to: 'compute.signal', reception: { buffer: 'latest', capacity: 1, overflow: 'drop_oldest', wait_timeout_ms: 100 } })
    const pull = guideDiagnostic(state, { file: 'model.json', message: 'a passive Pull input cannot declare an activating timeout' })
    expect(pull.fixes[0].command.description).toEqual({ mode: 'pull', root: 'Reading' })
    const retry = guideDiagnostic(state, { file: 'model.json', message: 'retry requires ack=received' })
    expect(retry.fixes[0].command).toMatchObject({ action: 'set', path: ['subscriptions', '0', 'qos', 'ack'], literal: '"received"' })
  })
  it('does not invent a precise target or correction for unknown or obsolete errors', () => {
    const state = project()
    expect(guideDiagnostic(state, { file: 'model.json', message: 'Unrecognized failure' })).toMatchObject({ targets: [{ path: [] }], fixes: [] })
    expect(guideDiagnostic(state, { file: 'model.json', message: 'latest requires capacity=1 and drop_oldest', revision: 'old' }).fixes).toEqual([])
    expect(guideDiagnostic(state, { message: 'Project recovery warning' }).targets).toEqual([])
  })
})

it('previews a proposed correction without applying it and disables it while source drafts exist', async () => {
  const pinia = createPinia(); setActivePinia(pinia)
  const store = useProject(); store.project = project()
  const command = vi.spyOn(store, 'command').mockResolvedValue(undefined), apply = vi.spyOn(store, 'apply')
  wrapper = mount(DiagnosticsView, { props: { diagnostics: [{ file: 'model.json', message: 'retry requires ack=received' }] }, global: { plugins: [pinia, [PrimeVue, { unstyled: true }]] } })
  await wrapper.findAll('button').find(button => button.text().startsWith('Preview:'))!.trigger('click')
  expect(command).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ action: 'set', literal: '"received"' }))
  expect(apply).not.toHaveBeenCalled()
  store.sourceDrafts['model.json'] = 'unfinished source'; await flushPromises()
  expect(wrapper.findAll('button').find(button => button.text().startsWith('Preview:'))!.attributes()).toHaveProperty('disabled')
})

it('supports keyboard palette navigation and routes port and source targets through the application', async () => {
  const pinia = createPinia(); setActivePinia(pinia)
  const store = useProject(); store.project = project()
  wrapper = mount(App, { attachTo: document.body, global: { plugins: [pinia, [PrimeVue, { unstyled: true }]], stubs: { GraphView: true, ComponentInspector: true, SourceEditor: true, SdlTree: true } } })
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, cancelable: true })); await flushPromises()
  const palette = wrapper.getComponent(NavigationPalette)
  expect(palette.props('visible')).toBe(true)
  const search = document.querySelector('[aria-label="Search project symbols"]') as HTMLInputElement
  search.value = 'signal'; search.dispatchEvent(new Event('input')); await flushPromises()
  search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true })); await flushPromises()
  expect(search.getAttribute('aria-activedescendant')).toBe('navigation-result-1')
  search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); await flushPromises()
  expect(store.editingComponent).toBe('compute')
  expect(store.selection?.path).toEqual(['components', 'compute', 'inputs', 'signal'])
  palette.vm.$emit('navigate', { file: 'types.sdl', path: [], sdl: '/0/0' }, true); await flushPromises()
  expect(store.editingComponent).toBe('')
  expect(wrapper.getComponent(SourceEditor).props('target')).toMatchObject({ file: 'types.sdl', sdl: '/0/0' })
})

it('uses code-point coordinates for SDL and syntax errors and preserves shifted source drafts', () => {
  const text = '// 🔎\nmessage Reading { uint32 value; }'
  const projection = { nodes: [{ path: '/0', start: Array.from('// 🔎\n').length }] }
  const target = { file: 'types.sdl', path: [], request: 1, sdl: '/0' }
  expect(sourcePosition(text, text, 'sdl', target, projection).position).toBe(text.indexOf('message'))
  expect(sourcePosition(text, text, 'sdl', { ...target, line: 2, column: 9 }, projection).position).toBe(text.indexOf('Reading'))
  expect(sourcePosition(text, text, 'sdl', target, { ...projection, stale: true })).toMatchObject({ position: null, notice: expect.stringContaining('stale') })
  expect(sourcePosition('\n' + text, text, 'sdl', target, projection)).toMatchObject({ position: null, notice: expect.stringContaining('draft is preserved') })
})
