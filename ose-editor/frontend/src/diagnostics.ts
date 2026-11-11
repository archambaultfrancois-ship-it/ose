import type { NavigationTarget } from './navigation'

export type Diagnostic = { severity?: string; origin?: string; message: string; file?: string; path?: string[]; line?: number; column?: number; revision?: string }
export type DiagnosticFix = { label: string; detail: string; command: Record<string, any> }
export type DiagnosticGuide = { explanation: string; targets: (NavigationTarget & { label: string })[]; fixes: DiagnosticFix[] }

export function currentDiagnostics(project: any, job: any): Diagnostic[] {
  const validation = job?.revision === project?.revision && !job?.stale ? job
    : project?.validation?.revision === project?.revision ? project.validation : null
  return [...(project?.diagnostics || []), ...(validation?.diagnostics || [])]
}

export function guideDiagnostic(project: any, diagnostic: Diagnostic): DiagnosticGuide {
  const file = diagnostic.file, doc = project?.documents?.[file || '']
  const guide: DiagnosticGuide = { explanation: diagnostic.origin === 'syntax'
    ? 'Repair the source syntax, preview and apply the source changes, then validate again.'
    : 'Review the original message, open the source or properties, then validate the updated revision.', targets: [], fixes: [] }
  if (!doc || !file) { guide.explanation = 'No editable source location was reported. Review the message and the project configuration.'; return guide }
  const add = (path: string[], label: string) => guide.targets.push({ file, path, label })
  const fallback = () => guide.targets.push({ file, path: diagnostic.path || [], line: diagnostic.line, column: diagnostic.column, label: diagnostic.line ? `Source · line ${diagnostic.line}${diagnostic.column ? ':' + diagnostic.column : ''}` : 'Source file' })
  if (diagnostic.revision && diagnostic.revision !== project.revision) {
    guide.explanation = 'This diagnostic belongs to an older revision. Validate again for an up-to-date location.'
    guide.targets.push({ file, path: [], label: 'Source file' }); return guide
  }
  if (diagnostic.origin === 'syntax') { fallback(); return guide }
  const model = file === project.model ? doc.value : null
  const components = model?.components || {}, subscriptions = Array.isArray(model?.subscriptions) ? model.subscriptions : []
  const message = diagnostic.message
  let match: RegExpMatchArray | null
  if ((match = message.match(/^unknown (inputs|outputs) endpoint: (.+)$/))) {
    const field = match[1] === 'inputs' ? 'to' : 'from', endpoint = match[2]
    subscriptions.forEach((sub: any, index: number) => { if (sub?.[field] === endpoint) add(['subscriptions', String(index), field], `${sub.id || index} · ${field}`) })
    guide.explanation = `The subscription references ${endpoint}, which is not a declared ${match[1] === 'inputs' ? 'input' : 'output'}. Choose an existing port or declare the missing port.`
  } else if ((match = message.match(/^timer input (.+) cannot receive a subscription$/))) {
    subscriptions.forEach((sub: any, index: number) => { if (sub?.to === match![1]) add(['subscriptions', String(index), 'to'], `${sub.id || index} · destination`) })
    guide.explanation = 'A timer is activated by time, rather than by incoming messages. Disconnect these subscriptions or choose a message input as their destination.'
  } else if ((match = message.match(/^subscriptions targeting (.+) must share reception settings:/))) {
    const [component, port] = match[1].split('.')
    if (components[component]?.inputs?.[port]) add(['components', component, 'inputs', port], match[1])
    guide.explanation = 'All producers feeding this input share one buffer. Use Reception to choose one common policy for all incoming subscriptions.'
  } else if (model && message === 'a passive Pull input cannot declare an activating timeout') {
    for (const [component, node] of Object.entries(components) as [string, any][]) for (const [port, desc] of Object.entries(node?.inputs || {}) as [string, any][]) {
      if (desc?.mode !== 'pull' || desc.kind === 'timer' || desc.timeout === undefined) continue
      add(['components', component, 'inputs', port], component + '.' + port)
      const { timeout, activates, ...description } = desc
      guide.fixes.push({ label: `Keep ${component}.${port} passive`, detail: 'Remove its timeout and activation permissions. The input remains Pull.', command: { action: 'configure_input', component, port, description } })
    }
    guide.explanation = 'Pull inputs expose cached data without triggering a callback. Keep Pull and remove timeout/activation declarations, or switch the input to Push in Execution.'
  } else if (model && message === 'latest requires capacity=1 and drop_oldest') {
    const targets = new Set<string>()
    subscriptions.forEach((sub: any, index: number) => {
      const qos = sub?.qos
      if (qos?.buffer !== 'latest' || (qos.capacity === undefined || qos.capacity === 1) && (qos.overflow === undefined || qos.overflow === 'drop_oldest')) return
      add(['subscriptions', String(index), 'qos'], `${sub.id || index} · reception`)
      const wait = qos.wait_timeout_ms ?? 100
      if (targets.has(sub.to) || typeof sub.to !== 'string' || !Number.isSafeInteger(wait) || wait <= 0) return
      targets.add(sub.to)
      guide.fixes.push({ label: `Normalize latest for ${sub.to}`, detail: 'Set capacity to 1 and overflow to drop_oldest for every incoming link to this input. Preserve ACK and retry settings.', command: { action: 'set_reception', to: sub.to, reception: { buffer: 'latest', capacity: 1, overflow: 'drop_oldest', wait_timeout_ms: wait } } })
    })
    guide.explanation = 'Latest retains a single message and replaces the previous one. It requires capacity 1 and drop_oldest for the shared input buffer.'
  } else if (model && message === 'retry requires ack=received') {
    subscriptions.forEach((sub: any, index: number) => {
      const retry = sub?.qos?.retry
      if (!retry || retry === 'none' || typeof retry !== 'object' || (retry.max_retries ?? 3) <= 0 || sub.qos.ack === 'received') return
      add(['subscriptions', String(index), 'qos'], `${sub.id || index} · delivery`)
      guide.fixes.push({ label: `Enable received ACK for ${sub.id || index}`, detail: 'Keep retries and request reception acknowledgements. Alternatively, disable retries in the subscription properties.', command: { action: 'set', file, path: ['subscriptions', String(index), 'qos', 'ack'], literal: '"received"' } })
    })
    guide.explanation = 'Retries need reception acknowledgements to detect delivery. Enable received ACK or disable retries.'
  } else if (model && /^(Application model v2 is required|expected a version 2 application model)/.test(message)) {
    add(['version'], 'Model version')
    guide.explanation = 'This editor authors model v2. Migrate the input and activation declarations in Sources before changing the version; changing the number alone does not migrate the model.'
  } else if (model && message === 'schemas must be a nonempty path') {
    add(['schemas'], 'Schema inputs')
    guide.explanation = 'Set schemas to the SDL source or schema directory, relative to the model file.'
  } else if (message === 'UDP cannot wait for capacity') {
    guide.explanation = 'UDP cannot block a sender while waiting for buffer capacity. Choose a different overflow policy in Reception, or use TCP in the deployment.'
  } else if (message === 'Select a deployment to validate the complete application') {
    guide.explanation = 'Choose a deployment in the header, then run Validate to check the complete application.'
  }
  if (!guide.targets.length) fallback()
  // Stale projections remain useful for source navigation, but cannot support repairs.
  if (doc.stale) guide.fixes = []
  return guide
}
