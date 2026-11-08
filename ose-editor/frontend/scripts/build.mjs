import { createHash } from 'node:crypto'
import { readFile, readdir, rm } from 'node:fs/promises'
import { spawn } from 'node:child_process'
const sources = ['index.html', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts']
async function collect(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = directory + '/' + entry.name
    if (entry.isDirectory()) await collect(path)
    else sources.push(path)
  }
}
await collect('src'); await collect('scripts')
async function fingerprint() {
  const hash = createHash('sha256')
  for (const path of sources.sort()) hash.update(path + '\0').update(await readFile(path)).update('\0')
  return hash.digest('hex')
}
const before = await fingerprint()
async function run(file, args) {
  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [file, ...args], { stdio: 'inherit' })
    child.on('error', reject)
    child.on('exit', code => code === 0 ? resolve() : reject(new Error('Build command failed: ' + file)))
  })
}
await run('node_modules/vue-tsc/bin/vue-tsc.js', ['--noEmit'])
await run('node_modules/vite/bin/vite.js', ['build'])
if (before !== await fingerprint()) {
  await rm('../src/ose_editor/static/asset-manifest.json', { force: true })
  throw new Error('Frontend sources changed during the build; rebuild before packaging')
}
process.env.OSE_EDITOR_SOURCE_HASH = before
await import('./licenses.mjs')
