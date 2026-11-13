// Wire candles keep the CSV's six fields; metadata stays outside the candle.
export function decodeCandleState(data, timeframe) {
  if (data.symbol !== 'AAPL' || data.timeframe !== timeframe || !['raw', 'polling'].includes(data.dataset) ||
      !['yahoo_finance_websocket', 'yahoo_finance_chart'].includes(data.source) ||
      !Array.isArray(data.candles) || !Array.isArray(data.metadata) || !data.feed_status ||
      !Number.isSafeInteger(data.sequence) || !Number.isFinite(Date.parse(data.as_of))) throw new Error('Invalid candle state')
  const metadata = new Map(data.metadata.map(item => [item.timestamp, item]))
  let previous = -Infinity
  const observations = data.candles.map(candle => {
    const time = Date.parse(candle.timestamp)
    const state = metadata.get(candle.timestamp)
    if (Object.keys(candle).sort().join(',') !== 'close,high,low,open,timestamp,volume' ||
        !Number.isFinite(time) || time <= previous || !state || typeof state.is_complete !== 'boolean' ||
        !['open', 'high', 'low', 'close'].every(key => Number.isFinite(candle[key]) && candle[key] >= 0) ||
        candle.low > Math.min(candle.open, candle.close) || candle.high < Math.max(candle.open, candle.close) ||
        (candle.volume !== null && (!Number.isSafeInteger(candle.volume) || candle.volume < 0))) throw new Error('Invalid candle')
    previous = time
    return { ...candle, event_time: candle.timestamp, observation_id: candle.timestamp,
      source: data.source, is_complete: state.is_complete, coverage: state }
  })
  return { observations, status: data.feed_status, sequence: data.sequence, asOf: Date.parse(data.as_of),
    dataset: data.dataset, lastArrival: data.last_recorded_arrival }
}
