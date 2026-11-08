import { readFile, readdir, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { join } from 'node:path'
const root = new URL('../', import.meta.url)
const lock = JSON.parse(await readFile(new URL('package-lock.json', root), 'utf8'))
const output = ['OSE Model Editor: third-party runtime notices\n']
for (const [path, pkg] of Object.entries(lock.packages)) {
  if (!path || pkg.dev) continue
  const base = new URL(path + '/', root)
  output.push(`\n${path} ${pkg.version}\nLicense: ${pkg.license || 'See package notices'}\n`)
  for (const name of (await readdir(base)).filter(name => /^(?:license|licence|copying|notice)(?:\.|$)/i.test(name))) {
    try { output.push(await readFile(new URL(name, base), 'utf8')) } catch { /* A directory is not a notice. */ }
  }
}
await writeFile(new URL('../src/ose_editor/static/licenses.txt', root), output.join('\n'))
const staticRoot = new URL('../src/ose_editor/static/', root)
const files = {}
async function inventory(prefix = '') {
  for (const entry of await readdir(new URL(prefix, staticRoot), { withFileTypes: true })) {
    const path = prefix + entry.name
    if (entry.isDirectory()) await inventory(path + '/')
    else if (path !== 'asset-manifest.json') files[path] = createHash('sha256').update(await readFile(new URL(path, staticRoot))).digest('hex')
  }
}
await inventory()
await writeFile(new URL('asset-manifest.json', staticRoot), JSON.stringify({ version: 1, source_hash: process.env.OSE_EDITOR_SOURCE_HASH || null, files }, null, 2) + '\n')
