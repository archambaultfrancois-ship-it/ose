import { test } from 'node:test'
import assert from 'node:assert/strict'
import { formatFileSize } from '../src/recording-size.mjs'

test('file sizes use decimal units and three significant digits', () => {
  for (const [bytes, expected] of [
    [0, '0.00 KB'], [123, '0.123 KB'], [1000, '1.00 KB'],
    [12345, '12.3 KB'], [999499, '999 KB'], [999500, '1.00 MB'],
    [12345678, '12.3 MB'], [999500000, '1.00 GB'],
    [1234567890, '1.23 GB'], ['12345678900', '12.3 GB'],
  ]) assert.equal(formatFileSize(bytes), expected)
})

test('unavailable sizes remain distinct from empty files', () => {
  for (const value of [undefined, null, '', ' ', NaN, Infinity, -1, 'invalid', false]) {
    assert.equal(formatFileSize(value), '—')
  }
})
