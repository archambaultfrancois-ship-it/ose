const billion = 1000000000n
export function secondsToNanoseconds(value: string): string {
  const match = /^(\d{1,11})(?:\.(\d{1,9}))?$/.exec(value.trim())
  if (!match) throw new Error('Enter nonnegative seconds with at most nine decimal places.')
  const result = BigInt(match[1]) * billion + BigInt((match[2] || '').padEnd(9, '0'))
  if (result > 18446744073709551615n) throw new Error('Time exceeds the supported simulation range.')
  return result.toString()
}
export function simulatedSeconds(value?: string | null): string {
  if (value == null) return '—'
  const ns = BigInt(value)
  const fraction = (ns % billion).toString().padStart(9, '0').replace(/0+$/, '')
  return (ns / billion).toString() + (fraction ? '.' + fraction : '')
}
