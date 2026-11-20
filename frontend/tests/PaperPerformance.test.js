import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import PaperPerformance from '../src/components/PaperPerformance.vue'

const priceTime = '2026-11-09T15:02:00.000000+00:00'
const valuation = {
  id: 4, event: 'completed_candle', event_time: priceTime,
  price_source: 'yahoo_finance_websocket_1m_close', price_time: priceTime,
  price_micros: 95_000_000, cash_micros: 710_000_000, shares: 3,
  realized_micros: 10_000_000, unrealized_micros: -15_000_000, total_micros: -5_000_000,
  average_cost_micros: 100_000_000, valuation_status: 'priced',
}

it('shows negative P&L and the candle valuation source and time', () => {
  const wrapper = mount(PaperPerformance, { props: { pnl: { current: valuation, history: [valuation] } } })
  expect(wrapper.get('[data-testid=paper-total_micros]').text()).toBe('-$5.00')
  expect(wrapper.get('tbody').text()).toContain('Completed 1m candle close')
  expect(wrapper.get('tbody').text()).toContain(priceTime)
  expect(wrapper.get('tbody').text()).toContain('-$15.00')
  expect(wrapper.get('tbody').text()).toContain('$710.00')
  wrapper.unmount()
})

it('labels unavailable open valuations while keeping valid earlier amounts visible', () => {
  const current = { ...valuation, valuation_status: 'unavailable', price_time: null,
    price_micros: null, unrealized_micros: null, total_micros: null }
  const wrapper = mount(PaperPerformance, { props: { pnl: { current, history: [valuation] } } })
  expect(wrapper.get('[role=status]').text()).toContain('Valuation unavailable')
  expect(wrapper.get('[data-testid=paper-realized_micros]').text()).toBe('$10.00')
  expect(wrapper.get('[data-testid=paper-total_micros]').text()).toBe('Unavailable')
  expect(wrapper.get('tbody').text()).toContain('-$5.00')
  wrapper.unmount()
})
