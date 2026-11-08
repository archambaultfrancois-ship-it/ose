import { scalarText } from './hints'

export function receptionFor(model: any, endpoint: string) {
  const subscriptions = (model?.subscriptions || []).map((sub: any, index: number) => ({ ...sub, index })).filter((sub: any) => sub.to === endpoint)
  const resolve = (sub: any) => {
    const qos = sub?.qos || {}, latest = qos.buffer === 'latest'
    return { buffer: qos.buffer || 'fifo', capacity: qos.capacity ?? (latest ? 1 : 1024),
      overflow: qos.overflow || (latest ? 'drop_oldest' : 'reject_new'), wait_timeout_ms: qos.wait_timeout_ms ?? 100 }
  }
  const policy = resolve(subscriptions[0])
  const signature = (value: any) => Object.values(value).map(scalarText).join('|')
  return { subscriptions, policy, conflict: subscriptions.some((sub: any) => signature(resolve(sub)) !== signature(policy)) }
}
