const units = ['KB', 'MB', 'GB']
const digits = new Intl.NumberFormat('en-US', {
  minimumSignificantDigits: 3, maximumSignificantDigits: 3, useGrouping: false,
})

/** Display decimal file sizes; the API may encode large byte counts as strings. */
export function formatFileSize(bytes) {
  if (!['number', 'string'].includes(typeof bytes) || String(bytes).trim() === '') return '—'
  const value = Number(bytes)
  if (!Number.isFinite(value) || value < 0) return '—'
  let size = value / 1000, unit = 0
  // Promote after rounding too: 999.5 KB should read 1.00 MB.
  while (unit < units.length - 1 && Number(size.toPrecision(3)) >= 1000) {
    size /= 1000
    unit++
  }
  return `${digits.format(size)} ${units[unit]}`
}
