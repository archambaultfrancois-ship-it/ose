import { reactive } from 'vue'

export const account = reactive<{ actor: string; csrf: string; permissions: Record<string, string[]>; expires_at: string }>(
  { actor: '', csrf: '', permissions: {}, expires_at: '' },
)
export class RequestError extends Error {
  constructor(message: string, public code: string, public details: any = {}) { super(message) }
}
export async function api(path: string, method = 'GET', data?: any, idempotent = false, raw = false, operationKey?: string): Promise<any> {
  const headers: Record<string, string> = {}
  if (method !== 'GET') headers['X-CSRF-Token'] = account.csrf
  if (data !== undefined) headers['Content-Type'] = 'application/json'
  if (idempotent) headers['Idempotency-Key'] = operationKey || crypto.randomUUID()
  const response = await fetch('/api/v1' + path, {
    method, headers, credentials: 'same-origin', body: data === undefined ? undefined : JSON.stringify(data),
  })
  if (response.status === 401) account.actor = ''
  if (!response.ok) {
    const result = await response.json().catch(() => ({}))
    throw new RequestError(result.error?.message || `Request failed (${response.status}).`, result.error?.code || 'HTTP_ERROR', result.error?.details)
  }
  if (raw) return response.text()
  if (response.status === 204) return null
  return response.json()
}
export async function allPages(path: string): Promise<any[]> {
  let cursor: string | null = null
  const items: any[] = []
  do {
    const page: any = await api(path + (path.includes('?') ? '&' : '?') + (cursor ? 'cursor=' + encodeURIComponent(cursor) : ''))
    items.push(...page.items)
    cursor = page.next
  } while (cursor)
  return items
}
const unresolvedCommands = new Map<string, string>()
export async function command(path: string, method = 'POST', data: any = {}): Promise<any> {
  const signature = JSON.stringify([account.actor, method, path, data])
  if (!unresolvedCommands.has(signature)) {
    if (unresolvedCommands.size >= 64) throw new RequestError('Too many unresolved actions. Review their outcomes and reload before issuing more commands.', 'COMMAND_QUOTA')
    unresolvedCommands.set(signature, crypto.randomUUID())
  }
  try {
    let operation = await api(path, method, data, true, false, unresolvedCommands.get(signature))
    for (let attempt = 0; operation.status === 'pending' && attempt < 90; attempt++) {
      await new Promise(resolve => setTimeout(resolve, Math.min(2000, 200 + attempt * 100)))
      operation = await api('/operations/' + operation.id)
    }
    if (operation.status === 'uncertain' || operation.status === 'closed_unknown') {
      throw new RequestError(`Operation ${operation.id}: result is undetermined. Read the current state before taking another action.`, 'UNCERTAIN')
    }
    if (operation.status === 'pending') return { pending: true, operation_id: operation.id }
    unresolvedCommands.delete(signature)
    if (operation.status === 'failed') throw new RequestError(operation.result?.error || 'The operation failed.', operation.result?.code || 'OPERATION_FAILED', operation.result?.details)
    return operation.result
  } catch (error) {
    if (error instanceof RequestError) throw error
    throw new RequestError('The command response was lost. Check the current state before retrying. Repeating this unchanged action will retrieve the original operation.', 'TRANSPORT_UNCERTAIN')
  }
}
export function can(permission: string, scope = '*'): boolean {
  return account.permissions.admin?.includes('*') || !!account.permissions[permission]?.some(p => p === '*' || p === scope)
}
export function download(text: string, name: string, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = name; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
