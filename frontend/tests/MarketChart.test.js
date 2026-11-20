import { mount } from '@vue/test-utils'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import MarketChart from '../src/components/MarketChart.vue'
import { candle } from './fixtures'

const api = vi.hoisted(() => {
  const series = () => ({ setData: vi.fn(), update: vi.fn(), priceScale: () => ({ applyOptions: vi.fn() }) })
  return {
    candles: series(), volume: series(), shortSma: series(), longSma: series(),
    seriesCount: 0, markers: { setMarkers: vi.fn(), detach: vi.fn() },
    scale: { fitContent: vi.fn(), getVisibleLogicalRange: vi.fn(() => ({ from: 0, to: 10 })), setVisibleLogicalRange: vi.fn() },
    remove: vi.fn(),
  }
})
vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'candles', HistogramSeries: 'volume', LineSeries: 'line', ColorType: { Solid: 'solid' },
  createSeriesMarkers: () => api.markers,
  createChart: () => ({
    addSeries(type) {
      if (type === 'line') return api.seriesCount++ === 0 ? api.shortSma : api.longSma
      return api[type]
    },
    timeScale: () => api.scale, panes: () => [{}, { setHeight: vi.fn() }], remove: api.remove, applyOptions: vi.fn(), subscribeCrosshairMove: vi.fn(),
  }),
}))
let wrapper
beforeEach(() => {
  api.seriesCount = 0
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
})
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals() })
it('plots OHLC and volume at source timestamps, then updates revisions', async () => {
  wrapper = mount(MarketChart, { props: { observations: [candle()] } })
  const time = Date.parse(candle().event_time) / 1000
  expect(api.candles.setData).toHaveBeenLastCalledWith([{ time, open: 100, high: 110, low: 90, close: 105 }])
  expect(api.volume.setData).toHaveBeenLastCalledWith([expect.objectContaining({ time, value: 1000 })])
  await wrapper.setProps({ observations: [candle({ revision: 1, close: 106, volume: 2000 })] })
  expect(api.candles.update).toHaveBeenLastCalledWith(expect.objectContaining({ time, close: 106 }))
  expect(api.volume.update).toHaveBeenLastCalledWith(expect.objectContaining({ time, value: 2000 }))
})
it('redraws older corrections while preserving chart range', async () => {
  const later = candle({ observation_id: 'later', event_time: '2026-01-02T14:01:00Z' })
  wrapper = mount(MarketChart, { props: { observations: [candle(), later] } })
  await wrapper.setProps({ observations: [candle({ revision: 1, close: 109 }), later] })
  expect(api.candles.setData).toHaveBeenLastCalledWith([expect.objectContaining({ close: 109 }), expect.objectContaining({ close: 105 })])
  expect(api.scale.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 0, to: 10 })
  wrapper.unmount(); wrapper = null
  expect(api.remove).toHaveBeenCalled()
})

it('renders unknown volume as whitespace rather than a zero histogram bar', () => {
  wrapper = mount(MarketChart, { props: { observations: [candle({ volume: null })] } })
  expect(api.volume.setData).toHaveBeenLastCalledWith([{ time: Date.parse(candle().event_time) / 1000 }])
})

it('draws the instructor example with warm-up gaps and only the two confirmed markers', async () => {
  const closes = [13, 10, 7, 13, 19, 7, 1]
  const observations = closes.map((close, index) => candle({
    event_time: `2026-11-09T14:3${index}:00Z`, open: close, high: close, low: close, close,
  }))
  const expected = [
    [null, null, null], [11.5, null, null], [8.5, 10, null], [10, 10, null],
    [16, 13, 'up'], [13, 13, null], [4, 9, 'down'],
  ]
  const points = observations.map(({ event_time }, index) => {
    const [short, long, crossover] = expected[index]
    return { timestamp: event_time, short, long, crossover }
  })
  const sma = { short_period: 2, long_period: 3, points }
  wrapper = mount(MarketChart, { props: { observations, sma } })
  const times = observations.map(candle => Date.parse(candle.event_time) / 1000)
  const series = period => times.map((time, index) => expected[index][period] === null
    ? { time } : { time, value: expected[index][period] })
  expect(api.shortSma.setData).toHaveBeenLastCalledWith(series(0))
  expect(api.longSma.setData).toHaveBeenLastCalledWith(series(1))
  expect(api.markers.setMarkers).toHaveBeenLastCalledWith([
    expect.objectContaining({ time: times[4], shape: 'arrowUp', position: 'belowBar', text: 'SMA cross up' }),
    expect.objectContaining({ time: times[6], shape: 'arrowDown', position: 'aboveBar', text: 'SMA cross down' }),
  ])

  await wrapper.setProps({ observations: observations.slice(5) })
  expect(api.markers.setMarkers).toHaveBeenLastCalledWith([expect.objectContaining({ time: times[6] })])
  await wrapper.setProps({ sma: null })
  expect(api.shortSma.setData).toHaveBeenLastCalledWith([])
  expect(api.longSma.setData).toHaveBeenLastCalledWith([])
  expect(api.markers.setMarkers).toHaveBeenLastCalledWith([])
})
