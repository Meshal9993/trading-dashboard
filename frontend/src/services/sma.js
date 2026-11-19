export const DEFAULT_SMA_PERIODS = Object.freeze({ short: 9, long: 21 })
export const MAX_SMA_PERIOD = 2000

export function validSmaPeriods({ short, long }) {
  return Number.isInteger(short) && Number.isInteger(long) && short >= 1 &&
    short < long && long <= MAX_SMA_PERIOD
}

export function appendSmaPeriods(query, periods) {
  if (periods) {
    query.set('sma_short', String(periods.short))
    query.set('sma_long', String(periods.long))
  }
  return query
}

export function decodeSma(sma, observations, periods) {
  if (!sma || (periods && (sma.short_period !== periods.short || sma.long_period !== periods.long))) return null
  if (!validSmaPeriods({ short: sma.short_period, long: sma.long_period }) ||
      !Array.isArray(sma.points) || sma.points.length !== observations.length) return null

  const validAverage = value => value === null || (Number.isFinite(value) && value >= 0)
  const validPoints = sma.points.every((point, index) => {
    const candle = observations[index]
    return point && point.timestamp === candle.event_time && validAverage(point.short) &&
      validAverage(point.long) && [null, 'up', 'down'].includes(point.crossover) &&
      (candle.is_complete || (point.short === null && point.long === null && point.crossover === null)) &&
      (point.crossover === null || (point.short !== null && point.long !== null))
  })
  return validPoints ? sma : null
}
