// Synthetic test fixtures only. Never imported into the application.
export const candle = (changes = {}) => ({ symbol: 'AAPL', source: 'yahoo_finance_chart', observation_id: 'AAPL:1m:1767362400', event_time: '2026-01-02T14:00:00Z', receipt_time: '2026-01-02T14:01:00Z', open: 100, high: 110, low: 90, close: 105, volume: 1000, revision: 0, is_complete: true, ...changes })
export const status = changes => ({ state: 'available', market_status: 'closed', is_realtime: false, last_successful_poll: '2026-01-02T14:02:00Z', ...changes })

export function candleState({ timeframe = '1m', sequence = 1, rows = [candle()], feedStatus = status(), dataset = 'raw', asOf = '2026-01-02T14:02:00Z' } = {}) {
  return { type: 'snapshot', symbol: 'AAPL', timeframe, sequence, as_of: asOf, dataset,
    source: dataset === 'raw' ? 'yahoo_finance_websocket' : 'yahoo_finance_chart',
    feed_status: { fallback_active: dataset === 'polling', ...feedStatus },
    candles: rows.map(row => ({ timestamp: row.event_time, open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume })),
    metadata: rows.map(row => ({ timestamp: row.event_time, is_complete: row.is_complete, available_minutes: 1, expected_minutes: 1 })) }
}
