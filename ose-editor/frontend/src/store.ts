import { defineStore } from 'pinia'
import { ref } from 'vue'

export type Selection = { file: string; path?: string[]; sdl?: string }
export const useProject = defineStore('project', () => {
  const project = ref<any>(null), selection = ref<Selection | null>(null)
  const token = ref(''), error = ref(''), pending = ref<any>(null), busy = ref(false)
  const job = ref<any>(null), sourceDrafts = ref<Record<string, string>>({})
  let stopped = false
  async function api(path: string, body?: any, method?: string) {
    const response = await fetch('/api/v1/' + path, {
      method: method || (body === undefined ? 'GET' : 'POST'),
      headers: { Authorization: 'Bearer ' + token.value, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    })
    const data = await response.json()
    if (!response.ok) { const failure = new Error(data.message || 'Request failed'); Object.assign(failure, { details: data }); throw failure }
    return data
  }
  async function guard(action: () => Promise<any>) {
    busy.value = true; error.value = ''
    try { return await action() } catch (e) { error.value = (e as Error).message; return undefined } finally { busy.value = false }
  }
  async function load() { return guard(async () => { project.value = await api('project'); if (!selection.value) selection.value = { file: project.value.model, path: [] } }) }
  async function command(command: any, preview = command.action !== 'workspace') {
    return guard(async () => {
      const prepared = await api('commands/prepare', { revision: project.value.revision, command })
      if (preview) { pending.value = prepared; return prepared }
      project.value = await api('commands/apply', { revision: prepared.revision, id: prepared.id })
      return project.value
    })
  }
  async function apply() {
    return guard(async () => {
      project.value = await api('commands/apply', { revision: pending.value.revision, id: pending.value.id })
      pending.value = null
      return project.value
    })
  }
  async function history(redo = false) { return guard(async () => { project.value = await api(redo ? 'redo' : 'undo', { revision: project.value.revision }) }) }
  async function save(allowInvalid = false) { return guard(async () => { project.value = await api('save', { revision: project.value.revision, allow_invalid: allowInvalid }) }) }
  async function validate(deployment?: string, schema?: string) {
    return guard(async () => {
      job.value = await api('validation', { revision: project.value.revision, deployment, schema })
      stopped = false
      while (!stopped && ['queued', 'running'].includes(job.value.status)) {
        await new Promise(resolve => setTimeout(resolve, 350))
        if (!stopped) job.value = await api('validation/' + job.value.id)
      }
      if (!stopped) project.value = await api('project')
    })
  }
  async function cancel() { stopped = true; if (job.value) job.value = await api('validation/' + job.value.id, undefined, 'DELETE') }
  function select(file: string, path?: string[], sdl?: string) { selection.value = { file, path, sdl } }
  return { project, selection, token, error, pending, busy, job, sourceDrafts, api, guard, load, command, apply, history, save, validate, cancel, select }
})
