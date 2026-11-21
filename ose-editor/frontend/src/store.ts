import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'

export type PortKind = 'message' | 'timer' | 'output'
export type Selection = { file: string; path?: string[]; sdl?: string }
export const useProject = defineStore('project', () => {
  const serverProject = ref<any>(null), selection = ref<Selection | null>(null)
  const modelTrail = ref<{ model: string; name: string; definition?: string }[]>([])
  const project = computed<any>({
    get() {
      const base = serverProject.value
      if (!base) return null
      const model = modelTrail.value.at(-1)?.model || base.model
      const context = base.model_contexts?.[model]
      const nested = model !== base.model
      const workspace = nested ? { positions: {}, groups: {}, views: {}, pinned: [], ...base.workspace.models?.[model] } : base.workspace
      const validation = nested && base.validation ? { ...base.validation,
        contracts: base.validation.model_contracts?.[model] || {},
        catalogues: { ...base.validation.catalogues, ...Object.fromEntries(Object.entries(base.schema_contexts?.[model] || {}).map(([source, token]) =>
          [source, base.validation.catalogues?.[String(token)] ? { ...base.validation.catalogues[String(token)], initial: {} } : undefined])) },
      } : base.validation
      return reactive({ ...base, validation, root_model: base.model, root_deployments: base.deployments, root_selected_deployment: base.selected_deployment,
        model, workspace, deployments: nested ? context?.deployments || [] : base.deployments,
        selected_deployment: nested ? workspace.selected_deployment || context?.deployments?.[0] : base.selected_deployment })
    },
    set(value) {
      serverProject.value = value
      if (modelTrail.value.some(item => !value?.documents[item.model])) {
        modelTrail.value = []; editingComponent.value = ''; portCreation.value = null
      }
    },
  })
  function openModel(file: string) {
    if (!serverProject.value?.model_contexts?.[file]) return
    editingComponent.value = ''; portCreation.value = null
    const previous = modelTrail.value.findIndex(item => item.model === file)
    if (previous >= 0) modelTrail.value = modelTrail.value.slice(0, previous + 1)
    else modelTrail.value = [{ model: serverProject.value.model, name: 'Application' }, ...(file === serverProject.value.model ? [] : [{ model: file, name: file }])]
    selection.value = { file, path: [] }
  }
  function openSubsystem(name: string) {
    const entry = project.value.subsystems?.[project.value.model]?.[name]
    if (!entry) return
    if (!modelTrail.value.length) modelTrail.value = [{ model: serverProject.value.model, name: 'Application' }]
    modelTrail.value.push({ model: entry.model, name, definition: entry.definition })
    editingComponent.value = ''; portCreation.value = null
    selection.value = { file: entry.model, path: [] }
  }
  function parentModel(index: number) {
    modelTrail.value = modelTrail.value.slice(0, index + 1)
    editingComponent.value = ''; portCreation.value = null
    selection.value = { file: project.value.model, path: [] }
  }
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
  async function connect() {
    return guard(async () => {
      const auth = await api('auth')
      if (!auth.auth_required) await load()
    })
  }
  async function command(command: any, preview = command.action !== 'workspace') {
    return guard(async () => {
      command = { model: command.action === 'select_deployment' ? serverProject.value.model : project.value.model, ...command }
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
      job.value = await api('validation', { revision: project.value.revision, deployment: deployment || serverProject.value.selected_deployment, schema })
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
    if (command.model && command.model !== project.value.model) return
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
  function select(file: string, path?: string[], sdl?: string) { if (serverProject.value?.model_contexts?.[file] && file !== project.value.model) openModel(file); portCreation.value = null; selection.value = { file, path, sdl } }
  return { project, modelTrail, openModel, openSubsystem, parentModel, selection, editingComponent, portCreation, editComponent, closeComponent, createPort, token, error, pending, busy, job, sourceDrafts, api, guard, connect, load, command, apply, history, save, validate, cancel, select }
})
