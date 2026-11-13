import { mount } from '@vue/test-utils'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import MarketChart from '../src/components/MarketChart.vue'
import { candle } from './fixtures'

const api = vi.hoisted(() => {
  const series = () => ({ setData: vi.fn(), update: vi.fn(), priceScale: () => ({ applyOptions: vi.fn() }) })
  return { candles: series(), volume: series(), scale: { fitContent: vi.fn(), getVisibleLogicalRange: vi.fn(() => ({ from: 0, to: 10 })), setVisibleLogicalRange: vi.fn() }, remove: vi.fn() }
})
vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'candles', HistogramSeries: 'volume', ColorType: { Solid: 'solid' },
  createChart: () => ({ addSeries: type => type === 'candles' ? api.candles : api.volume, timeScale: () => api.scale, panes: () => [{}, { setHeight: vi.fn() }], remove: api.remove, applyOptions: vi.fn(), subscribeCrosshairMove: vi.fn() }),
}))
let wrapper
beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }))
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
