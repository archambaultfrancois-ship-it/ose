import { afterEach, describe, expect, it, vi } from 'vitest'
import { account, command } from '../src/api'

afterEach(() => vi.unstubAllGlobals())
describe('command recovery after a lost reply', () => {
  it('retains a field pointer when a command fails validation', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ id: 'invalid', status: 'failed', result: {
      error: 'Invalid value for SDL type uint32.', code: 'PARAMETER_INVALID', details: { pointer: '/machine_ids/1' },
    } }), { status: 200 })))
    await expect(command('/parameters/invalid', 'PUT', { value: { machine_ids: [-1] } })).rejects.toMatchObject({
      code: 'PARAMETER_INVALID', details: { pointer: '/machine_ids/1' },
    })
  })
  it('does not retry automatically and reuses the key on an explicit unchanged retry', async () => {
    account.actor = 'transport-test'
    const keys: string[] = []
    const fetch = vi.fn(async (_path: string, options: any) => {
      keys.push(options.headers['Idempotency-Key'])
      if (keys.length === 1) throw new TypeError('Connection closed after dispatch')
      return new Response(JSON.stringify({ id: 'same-operation', status: 'complete', result: { result: 'stored' } }), { status: 200 })
    })
    vi.stubGlobal('fetch', fetch)
    await expect(command('/parameters/transport', 'PUT', { value: 'first' })).rejects.toMatchObject({ code: 'TRANSPORT_UNCERTAIN' })
    expect(fetch).toHaveBeenCalledTimes(1)
    await expect(command('/parameters/transport', 'PUT', { value: 'first' })).resolves.toEqual({ result: 'stored' })
    expect(keys[1]).toBe(keys[0])
    await command('/parameters/transport', 'PUT', { value: 'first' })
    expect(keys[2]).not.toBe(keys[1])
  })

  it('keeps an uncertain operation bound to its original key', async () => {
    account.actor = 'uncertain-test'
    const keys: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (_path: string, options: any) => {
      keys.push(options.headers['Idempotency-Key'])
      return new Response(JSON.stringify({ id: 'uncertain-operation', status: 'uncertain', result: null }), { status: 200 })
    }))
    for (let n = 0; n < 2; n++) await expect(command('/parameters/uncertain', 'PUT', { value: 'same' })).rejects.toMatchObject({ code: 'UNCERTAIN' })
    expect(keys[1]).toBe(keys[0])
  })

  it('treats a changed body as a separate explicit action', async () => {
    account.actor = 'changed-test'
    const keys: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (_path: string, options: any) => {
      keys.push(options.headers['Idempotency-Key'])
      if (keys.length === 1) throw new TypeError('Lost reply')
      return new Response(JSON.stringify({ id: 'new-operation', status: 'complete', result: { result: 'stored' } }), { status: 200 })
    }))
    await expect(command('/parameters/changed', 'PUT', { value: 'first' })).rejects.toMatchObject({ code: 'TRANSPORT_UNCERTAIN' })
    await command('/parameters/changed', 'PUT', { value: 'second' })
    expect(keys[1]).not.toBe(keys[0])
  })
})
