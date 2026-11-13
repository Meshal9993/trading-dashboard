import { describe, expect, it } from 'vitest'
import { ageSeconds, chartData, formatAge, mergeObservations } from '../src/services/observations'
import { candle } from './fixtures'

describe('observation ordering and provenance', () => {
  it('keeps highest revisions and the first receipt of replays', () => {
    const revised = candle({ revision: 2, close: 108 })
    const rows = mergeObservations([revised], [candle(), candle({ revision: 1 }), candle({ revision: 2, receipt_time: '2026-01-02T16:00:00Z' })])
    expect(rows).toEqual([revised])
  })
  it('sorts late candles and preserves source timestamps', () => {
    const later = candle({ observation_id: 'later', event_time: '2026-01-02T14:01:00Z' })
    const rows = mergeObservations([later], [candle()])
    expect(rows.map(row => row.observation_id)).toEqual([candle().observation_id, 'later'])
    expect(rows[0].event_time).toBe(candle().event_time)
  })
  it.each([{ symbol: 'MSFT' }, { source: 'fake' }, { volume: null }, { close: NaN }, { revision: -1 }, { event_time: 'invalid' }, { high: 95 }])('rejects malformed or unrelated data: %j', changes => {
    expect(() => mergeObservations([], [candle(changes)])).toThrow()
  })
  it('ages data without receiving another observation', () => {
    const start = Date.parse(candle().event_time)
    expect(ageSeconds([candle()], start + 200000)).toBe(200)
    expect(ageSeconds([], start)).toBeNull()
    expect(formatAge(90061)).toBe('1d 1h')
    expect(formatAge(null)).toBe('Not available')
  })
  it('provides unique increasing chart timestamps with matching OHLCV', () => {
    const rows = chartData([candle(), candle({ observation_id: 'same-time', revision: 2, close: 108 })])
    expect(rows).toHaveLength(1)
    expect(rows[0].time).toBe(Date.parse(candle().event_time) / 1000)
    expect(rows[0].close).toBe(108)
    expect(rows[0].volume).toBe(1000)
  })
})
