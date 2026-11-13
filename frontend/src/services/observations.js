export const SOURCE = 'yahoo_finance_chart'

export function validObservation(item) {
  return item && item.symbol === 'AAPL' && item.source === SOURCE &&
    typeof item.observation_id === 'string' && item.observation_id.length > 0 &&
    Number.isFinite(Date.parse(item.event_time)) && Number.isFinite(Date.parse(item.receipt_time)) &&
    Number.isSafeInteger(item.revision) && item.revision >= 0 && typeof item.is_complete === 'boolean' &&
    ['open', 'high', 'low', 'close'].every(key => Number.isFinite(item[key]) && item[key] >= 0) &&
    Number.isSafeInteger(item.volume) && item.volume >= 0 &&
    item.low <= Math.min(item.open, item.close) && item.high >= Math.max(item.open, item.close)
}

export function mergeObservations(current, incoming) {
  if (!Array.isArray(incoming) || !incoming.every(validObservation)) throw new Error('Invalid observations')
  const map = new Map(current.map(item => [item.observation_id, item]))
  for (const item of incoming) {
    const previous = map.get(item.observation_id)
    if (!previous || item.revision > previous.revision) map.set(item.observation_id, item)
  }
  return [...map.values()].sort((a, b) => Date.parse(a.event_time) - Date.parse(b.event_time) || a.observation_id.localeCompare(b.observation_id)).slice(-2000)
}

export function ageSeconds(observations, now) {
  const latest = observations.at(-1)
  return latest ? Math.max(0, Math.floor((now - Date.parse(latest.event_time)) / 1000)) : null
}

export function formatAge(seconds) {
  if (seconds === null) return 'Not available'
  if (seconds < 60) return `${Math.floor(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`
  return `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h`
}

export function chartData(observations) {
  // The chart requires strictly increasing, unique timestamps.
  const byTime = new Map()
  for (const item of observations) {
    const time = Math.floor(Date.parse(item.event_time) / 1000)
    const previous = byTime.get(time)
    if (!previous || item.revision > previous.revision) byTime.set(time, item)
  }
  return [...byTime.entries()].sort(([a], [b]) => a - b).map(([time, item]) => ({ ...item, time }))
}
