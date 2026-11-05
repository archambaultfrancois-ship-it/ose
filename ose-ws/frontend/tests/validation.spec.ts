import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'
import ParametersPage from '../src/components/ParametersPage.vue'
import SchemaField from '../src/components/SchemaField.vue'

const cleanups: (() => void)[] = []
afterEach(() => { cleanups.splice(0).forEach(cleanup => cleanup()); document.body.innerHTML = ''; vi.unstubAllGlobals() })
function mount(render: () => any) {
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp({ render })
  app.mount(root)
  cleanups.push(() => app.unmount())
  return root
}
function button(root: Element, label: string) {
  const result = [...root.querySelectorAll('button')].find(node => node.textContent?.includes(label))
  if (!result) throw new Error(`Button ${label} not found`)
  return result as HTMLButtonElement
}
async function until(predicate: () => boolean) {
  for (let n = 0; n < 100 && !predicate(); n++) { await new Promise(resolve => setTimeout(resolve, 5)); await nextTick() }
  expect(predicate()).toBe(true)
}
function input(root: Element, label: string) {
  const result = [...root.querySelectorAll('input')].find(node => node.getAttribute('aria-label') === label)
  if (!result) throw new Error(`Input ${label} not found`)
  return result
}
async function enter(element: HTMLInputElement, text: string) {
  element.value = text
  element.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
}

it('reveals and focuses an invalid paginated array entry and clears it only when that value changes', async () => {
  const schema = { root: 'Settings', schema_digest: 'digest', presentation: {}, types: {
    Settings: { kind: 'message', fields: [
      { id: 1, name: 'label', type: 'string', cardinality: 'optional', dimensions: [] },
      { id: 2, name: 'nested', type: 'Nested', cardinality: 'required', dimensions: [] },
    ] },
    Nested: { kind: 'message', fields: [{ id: 1, name: 'items', type: 'uint32', cardinality: 'repeated', dimensions: [] }] },
  } }
  const initial = { label: 'Initial label', nested: { items: Array.from({ length: 50 }, (_, n) => n) } }
  let validations = 0, writes = 0
  vi.stubGlobal('fetch', vi.fn(async (path: string, options: any = {}) => {
    let result: any, status = 200
    if (path.endsWith('/schema')) result = schema
    else if (path.endsWith('/validate')) {
      validations++
      const { value } = JSON.parse(options.body)
      if (value.nested.items[45] < 0) {
        status = 422
        result = { error: { code: 'PARAMETER_INVALID', message: 'Invalid value for SDL type uint32.', details: { pointer: '/nested/items/45' } } }
      } else result = { value, schema_digest: 'digest', preview_hash: 'preview', encoded_bytes: 100 }
    } else if (options.method === 'PUT') { writes++; result = { id: 'operation', status: 'complete', result: { result: 'stored' } } }
    else result = { value: initial, schema_digest: 'digest' }
    return new Response(JSON.stringify(result), { status })
  }))
  const root = mount(() => h(ParametersPage, { blocks: [{ id: 'block', path: 'machine/settings', permissions: { read: true, write: true } }], change: null, connected: true }))
  button(root, 'settings').click()
  await until(() => !!root.querySelector('[aria-label="label"]'))
  button(root, 'Next 40').click()
  await nextTick()
  await enter(input(root, '[45]'), '-1')
  button(root, 'Previous 40').click()
  await nextTick()
  for (const branch of root.querySelectorAll('.schema-field details')) branch.open = false
  button(root, 'Apply').click()
  await until(() => !!root.querySelector('[aria-invalid="true"]'))
  const invalid = input(root, '[45]')
  expect(invalid.getAttribute('aria-invalid')).toBe('true')
  expect(invalid.closest('.schema-field')?.classList.contains('invalid')).toBe(true)
  expect(root.querySelectorAll('.schema-field.invalid')).toHaveLength(1)
  expect(root.querySelectorAll('[aria-invalid="true"]')).toHaveLength(1)
  expect(document.activeElement).toBe(invalid)
  const description = document.getElementById(invalid.getAttribute('aria-describedby')!)
  expect(description?.textContent).toContain('Invalid value for SDL type uint32.')
  let ancestor = invalid.parentElement
  while (ancestor && !ancestor.classList.contains('parameter-editor')) {
    if (ancestor.tagName === 'DETAILS') expect((ancestor as HTMLDetailsElement).open).toBe(true)
    ancestor = ancestor.parentElement
  }
  expect(root.querySelector('.parameter-editor .validation-summary[role="alert"]')?.textContent).toContain('Parameter error')
  expect(writes).toBe(0)
  expect(button(root, 'Apply').disabled).toBe(false)
  await enter(input(root, 'label'), 'Unrelated edit')
  expect(input(root, '[45]').getAttribute('aria-invalid')).toBe('true')
  button(root, 'Previous 40').click()
  await nextTick()
  expect([...root.querySelectorAll('input')].some(node => node.getAttribute('aria-label') === '[45]')).toBe(false)
  button(root, 'Go to field').click()
  await until(() => document.activeElement?.getAttribute('aria-label') === '[45]')
  await enter(input(root, '[45]'), '45')
  expect(root.querySelector('[aria-invalid="true"]')).toBeNull()
  expect(root.querySelector('.validation-summary')).toBeNull()
  expect(validations).toBe(1)
  expect(button(root, 'Apply').disabled).toBe(false)
  button(root, 'Apply').click()
  await until(() => !!root.textContent?.includes('Stored by OSE.'))
  expect(validations).toBe(2)
  expect(writes).toBe(1)
})

it('marks escaped optional fields and the exact complex part without marking their siblings', async () => {
  const schema = { types: { Root: { kind: 'message', fields: [
    { id: 1, name: 'a/b~c', type: 'string', cardinality: 'optional', dimensions: [] },
    { id: 2, name: 'phasor', type: 'c32', cardinality: 'required', dimensions: [] },
  ] } }, presentation: {} }
  const props = reactive({ schema, type: 'Root', label: 'Value', modelValue: { 'a/b~c': 'label', phasor: { real: 0, imag: 1 } },
    validation: { pointer: '/a~1b~0c', message: 'Label is too long.' } })
  const root = mount(() => h(SchemaField, props))
  const label = input(root, 'a/b~c')
  expect(label.getAttribute('aria-invalid')).toBe('true')
  expect(document.getElementById(label.getAttribute('aria-describedby')!)?.textContent).toContain('Label is too long.')
  props.validation = { pointer: '/phasor/imag', message: 'Imaginary value exceeds its range.' }
  await nextTick()
  expect(label.getAttribute('aria-invalid')).toBeNull()
  const real = root.querySelector('[data-pointer="/phasor/real"]')!
  const imag = root.querySelector('[data-pointer="/phasor/imag"]')!
  expect(real.getAttribute('aria-invalid')).toBeNull()
  expect(imag.getAttribute('aria-invalid')).toBe('true')
  expect(root.querySelectorAll('.schema-field.invalid')).toHaveLength(1)
})

it('keeps a block-level failure visible near the editor controls without guessing a field', async () => {
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    const status = path.endsWith('/validate') ? 503 : 200
    const data = path.endsWith('/schema') ? { root: 'Settings', types: { Settings: { kind: 'message', fields: [] } } }
      : status === 503 ? { error: { message: 'OSE is unavailable.', details: {} } } : { value: {} }
    return new Response(JSON.stringify(data), { status })
  }))
  const root = mount(() => h(ParametersPage, { blocks: [{ id: 'block', path: 'settings', permissions: { read: true, write: true } }], change: null, connected: true }))
  button(root, 'settings').click()
  await until(() => !!root.querySelector('.parameter-editor'))
  button(root, 'Apply').click()
  await until(() => !!root.querySelector('.validation-summary'))
  expect(root.querySelector('.validation-summary')?.textContent).toContain('OSE is unavailable.')
  expect(root.querySelector('.schema-field.invalid')).toBeNull()
  expect(document.activeElement).toBe(root.querySelector('.validation-summary'))
})
