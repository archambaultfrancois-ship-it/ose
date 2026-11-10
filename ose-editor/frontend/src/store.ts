import { defineStore } from 'pinia'
import { ref } from 'vue'

export type PortKind = 'message' | 'timer' | 'output'
export type Selection = { file: string; path?: string[]; sdl?: string }
export const useProject = defineStore('project', () => {
  const project = ref<any>(null), selection = ref<Selection | null>(null)
  const token = ref(''), error = ref(''), pending = ref<any>(null), busy = ref(false)
  const job = ref<any>(null), sourceDrafts = ref<Record<string, string>>({})
  const editingComponent = ref(''), portCreation = ref<{ kind: PortKind; request: number } | null>(null)
  let creationRequest = 0
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
      if (preview) { pending.value = { ...prepared, editorCommand: command }; return prepared }
      project.value = await api('commands/apply', { revision: prepared.revision, id: prepared.id })
      return project.value
    })
  }
  async function apply() {
    return guard(async () => {
      const command = pending.value.editorCommand
      project.value = await api('commands/apply', { revision: pending.value.revision, id: pending.value.id })
      pending.value = null
      followEdit(command)
      return project.value
    })
  }
  async function history(redo = false) { return guard(async () => { project.value = await api(redo ? 'redo' : 'undo', { revision: project.value.revision }); reconcileEditor() }) }
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
  function editComponent(name: string) {
    editingComponent.value = name; portCreation.value = null
    select(project.value.model, ['components', name])
  }
  function closeComponent() {
    const name = editingComponent.value
    editingComponent.value = ''; portCreation.value = null
    if (project.value.documents[project.value.model]?.value?.components?.[name]) select(project.value.model, ['components', name])
  }
  function createPort(kind: PortKind) {
    select(project.value.model, ['components', editingComponent.value])
    portCreation.value = { kind, request: ++creationRequest }
  }
  function reconcileEditor() {
    if (!editingComponent.value) return
    const node = project.value.documents[project.value.model]?.value?.components?.[editingComponent.value]
    if (!node) { closeComponent(); return }
    const path = selection.value?.path || []
    if (path[0] === 'components' && path[1] === editingComponent.value && path.length >= 4 && !node[path[2]]?.[path[3]])
      select(project.value.model, ['components', editingComponent.value])
  }
  function followEdit(command: any) {
    if (!command || !editingComponent.value) return
    portCreation.value = null
    if (command.action === 'rename' && command.kind === 'component' && command.old === editingComponent.value) {
      editComponent(command.name)
    } else if (command.action === 'rename' && command.kind === 'port' && command.old.startsWith(editingComponent.value + '.')) {
      select(project.value.model, ['components', editingComponent.value, command.direction, command.name])
    } else if (command.component === editingComponent.value) {
      if (command.action === 'configure_activation') {
        const desc = project.value.documents[project.value.model].value.components[command.component].inputs[command.input]
        const index = desc.activates?.[command.event]?.indexOf(command.output)
        select(project.value.model, ['components', command.component, 'inputs', command.input,
          ...(command.enabled && index >= 0 ? ['activates', command.event, String(index)] : [])])
      } else if (['configure_input', 'configure_output', 'duplicate_port'].includes(command.action)) {
        select(project.value.model, ['components', command.component,
          command.action === 'configure_input' ? 'inputs' : command.action === 'configure_output' ? 'outputs' : command.direction, command.port])
      } else if (command.action === 'remove_port') editComponent(command.component)
    }
    reconcileEditor()
  }
  function select(file: string, path?: string[], sdl?: string) { portCreation.value = null; selection.value = { file, path, sdl } }
  return { project, selection, editingComponent, portCreation, editComponent, closeComponent, createPort, token, error, pending, busy, job, sourceDrafts, api, guard, load, command, apply, history, save, validate, cancel, select }
})
