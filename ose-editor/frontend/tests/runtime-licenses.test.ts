import { describe, expect, it } from 'vitest'
import lock from '../package-lock.json'

describe('editor UI dependencies', () => {
  it('keeps the PrimeVue and theme runtime packages under MIT', () => {
    const packages = Object.entries(lock.packages).filter(([path, pkg]) =>
      !('dev' in pkg && pkg.dev) && /node_modules\/(?:primevue(?:$|\/)|@primevue\/|@primeuix\/|@primeui\/)/.test(path))
    expect(packages.length).toBeGreaterThan(0)
    for (const [path, pkg] of packages) expect('license' in pkg && pkg.license, path).toBe('MIT')
  })

  it('does not bring a license-key manager into the editor', () => {
    expect(Object.keys(lock.packages).filter(path => path.endsWith('/@primeui/license-manager'))).toEqual([])
  })
})
