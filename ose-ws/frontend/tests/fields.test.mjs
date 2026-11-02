import { test } from 'node:test'
import assert from 'node:assert/strict'
import { defaultValue, inputValue, changes, makeTree, presentationAt } from '../src/fields.mjs'

test('64-bit integer text never passes through Number', () => {
  assert.equal(inputValue('uint64', '18446744073709551615'), '18446744073709551615')
  assert.equal(inputValue('int64', '-9223372036854775808'), '-9223372036854775808')
  assert.equal(inputValue('fl32', '1.25e3'), 1250)
  assert.equal(inputValue('fl64', ''), '')
})
test('optional fields and fixed arrays keep SDL structure', () => {
  const schema = { types: { Settings: { kind: 'message', fields: [
    { name: 'counter', type: 'uint64', cardinality: 'required', dimensions: [] },
    { name: 'label', type: 'string', cardinality: 'optional', dimensions: [] },
    { name: 'matrix', type: 'fl64', cardinality: 'required', dimensions: [2, 3] },
    { name: 'samples', type: 'int64', cardinality: 'repeated', dimensions: [] },
  ] } } }
  assert.deepEqual(defaultValue(schema, 'Settings'), { counter: '0', label: null, matrix: [[0,0,0],[0,0,0]], samples: [] })
})
test('field differences preserve exact integers and escaped JSON pointers', () => {
  assert.deepEqual(changes({ 'a/b': '9223372036854775807' }, { 'a/b': '9223372036854775806' }), [
    { pointer: '/a~1b', before: '9223372036854775807', after: '9223372036854775806' },
  ])
  assert.deepEqual(changes({ value: null }, { value: 0 }), [{ pointer: '/value', before: null, after: 0 }])
})
test('catalogues build dotted and slash trees without losing leaf prefixes', () => {
  const tree = makeTree([{ id: 'a', name: 'source.signal' }, { id: 'b', name: 'source.signal.raw' }], 'name', '.')
  assert.equal(tree[0].children[0].item.id, 'a')
  assert.equal(tree[0].children[0].children[0].item.id, 'b')
  assert.equal(makeTree([{ path: 'machine/settings' }], 'path', '/')[0].children[0].label, 'settings')
})
test('presentation metadata matches generic array elements and exact overrides', () => {
  const schema = { presentation: { '/channels/*/gain': { unit: 'dB' }, '/channels/2/gain': { unit: 'exact' } } }
  assert.equal(presentationAt(schema, '/channels/0/gain').unit, 'dB')
  assert.equal(presentationAt(schema, '/channels/2/gain').unit, 'exact')
  assert.deepEqual(presentationAt(schema, '/channels/not-an-index/gain'), {})
})
