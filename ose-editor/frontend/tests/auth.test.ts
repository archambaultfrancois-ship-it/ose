// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useProject } from '../src/store'

afterEach(() => vi.unstubAllGlobals())

it('opens a local project automatically when authentication is disabled', async () => {
  setActivePinia(createPinia())
  const fetch = vi.fn(async (path: string) => new Response(JSON.stringify(
    path.endsWith('/auth') ? { auth_required: false } : { model: 'model.json', documents: {} },
  )))
  vi.stubGlobal('fetch', fetch)
  const store = useProject()
  await store.connect()
  expect(fetch.mock.calls.map(call => call[0])).toEqual(['/api/v1/auth', '/api/v1/project'])
  expect(store.project.model).toBe('model.json')
  expect(store.token).toBe('')
})

it('waits for the access token when authentication is enabled', async () => {
  setActivePinia(createPinia())
  const fetch = vi.fn(async () => new Response(JSON.stringify({ auth_required: true })))
  vi.stubGlobal('fetch', fetch)
  const store = useProject()
  await store.connect()
  expect(fetch).toHaveBeenCalledOnce()
  expect(store.project).toBeNull()
  expect(store.error).toBe('')
})
