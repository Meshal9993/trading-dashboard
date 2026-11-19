import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'

import BacktestView from '../src/components/BacktestView.vue'

const beginning = '2026-11-09T14:30:00Z'
const signalTime = '2026-11-09T14:35:00Z'
const receipt = '2026-11-09T14:35:04Z'
const input = {
  symbol: 'AAPL', start_date: '2026-11-09', end_date: '2026-11-09',
  initial_cash: '1000.00', short_period: 2, long_period: 3,
}
const result = {
  ...input,
  initial_cash_micros: 1_000_000_000,
  completed_candles: 7,
  as_of: '2026-11-09T14:37:11Z',
  signals: [{ side: 'up', candle_time: '2026-11-09T14:34:00Z', decision_time: signalTime, short: 16, long: 13, order_id: 1 }],
  orders: [{ id: 1, side: 'buy', quantity: 1, status: 'filled', submitted_at: signalTime, rejection_reason: null }],
  fills: [{ order_id: 1, side: 'buy', quantity: 1, price_micros: 7_000_000,
    source_time: '2026-11-09T14:35:02Z', receipt_time: receipt, arrival_sequence: 6 }],
  performance: { cash_micros: 993_000_000, shares: 1, average_cost_micros: 7_000_000,
    realized_micros: 0, unrealized_micros: -6_000_000, total_micros: -6_000_000,
    equity_micros: 994_000_000, price_micros: 1_000_000, price_time: '2026-11-09T14:37:00Z' },
  equity_curve: [{ timestamp: beginning, equity_micros: 1_000_000_000 },
    { timestamp: receipt, equity_micros: 1_000_000_000 }, { timestamp: '2026-11-09T14:37:00Z', equity_micros: 994_000_000 }],
  warnings: ['One order remained pending at the end.'],
}
const reply = (payload, ok = true) => ({ ok, json: async () => payload })

let wrapper
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.unstubAllGlobals()
})

async function form(fetch, options = {}) {
  vi.stubGlobal('fetch', fetch)
  wrapper = mount(BacktestView)
  const dates = wrapper.findAll('input[type=date]')
  const periods = wrapper.findAll('input[type=number]')
  await dates[0].setValue(options.start ?? input.start_date)
  await dates[1].setValue(options.end ?? input.end_date)
  await periods[0].setValue(options.short ?? String(input.short_period))
  await periods[1].setValue(options.long ?? String(input.long_period))
  await wrapper.get('input[inputmode=decimal]').setValue(options.cash ?? input.initial_cash)
  return wrapper.get('form')
}

it('runs the selected strategy and displays an equity curve, fills and timestamped signals', async () => {
  let finish
  const fetch = vi.fn(() => new Promise(resolve => { finish = resolve }))
  const settings = await form(fetch)
  await settings.trigger('submit')
  expect(wrapper.get('[role=status]').text()).toBe('Running backtest…')
  expect(wrapper.get('button[type=submit]').element.disabled).toBe(true)
  expect(fetch).toHaveBeenCalledWith('/backtest/run', expect.objectContaining({
    method: 'POST', body: JSON.stringify(input),
  }))
  finish(reply(result))
  await flushPromises()

  expect(wrapper.get('[data-testid=backtest-equity]').text()).toBe('$994.00')
  expect(wrapper.get('[data-testid=backtest-total]').text()).toBe('-$6.00')
  expect(wrapper.get('svg[role=img]').attributes('aria-label')).toContain('from $1,000.00 to $994.00')
  expect(wrapper.get('svg polyline').attributes('points').split(' ')).toHaveLength(3)
  const details = wrapper.get('details')
  details.element.open = true
  await details.trigger('toggle')
  expect(wrapper.get('details table').text()).toContain('$994.00')
  const tables = wrapper.findAll('.backtest-ledger table')
  expect(tables[0].text()).toContain('$7.00')
  expect(tables[0].text()).toContain('14:35:04')
  expect(tables[1].text()).toContain('filled')
  expect(tables[2].text()).toContain('Upward')
  expect(tables[2].text()).toContain('14:35:00')
  expect(wrapper.get('[aria-label="Backtest warnings"]').text()).toContain('One order remained pending')
  expect(wrapper.get('button[type=submit]').element.disabled).toBe(false)
})

it.each([
  [{ end: '2026-11-08' }, 'End date must be on or after start date.'],
  [{ cash: '0' }, 'Enter positive initial cash'],
  [{ short: '3', long: '3' }, 'with a smaller short period'],
])('blocks invalid date, cash, and SMA inputs without sending a request', async (options, message) => {
  const fetch = vi.fn()
  const settings = await form(fetch, options)
  await settings.trigger('submit')
  expect(wrapper.get('[role=alert]').text()).toContain(message)
  expect(fetch).not.toHaveBeenCalled()
})

it('clears the previous run while rerunning and shows an API validation error', async () => {
  let rejectRun
  const fetch = vi.fn().mockResolvedValueOnce(reply(result))
    .mockImplementationOnce(() => new Promise(resolve => { rejectRun = resolve }))
  const settings = await form(fetch)
  await settings.trigger('submit')
  await flushPromises()
  expect(wrapper.find('[data-testid=backtest-equity]').exists()).toBe(true)
  await settings.trigger('submit')
  expect(wrapper.find('[data-testid=backtest-equity]').exists()).toBe(false)
  rejectRun(reply({ detail: [{ msg: 'Unsupported range' }] }, false))
  await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toBe('Unsupported range')
  expect(wrapper.find('[data-testid=backtest-equity]').exists()).toBe(false)
})

it('identifies an empty historical run and keeps the initial cash', async () => {
  const fetch = vi.fn().mockResolvedValue(reply({
    ...result, completed_candles: 0, as_of: null, signals: [], orders: [], fills: [], equity_curve: [],
    performance: { ...result.performance, equity_micros: 1_000_000_000, cash_micros: 1_000_000_000,
      shares: 0, average_cost_micros: null, unrealized_micros: 0, total_micros: 0, price_time: null, price_micros: null },
    warnings: ['No saved observations were found.'],
  }))
  const settings = await form(fetch)
  await settings.trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('No completed candles in this range.')
  expect(wrapper.text()).toContain('No equity points for this range.')
  expect(wrapper.text()).toContain('No simulated fills.')
  expect(wrapper.get('[data-testid=backtest-equity]').text()).toBe('$1,000.00')
})

it('aborts an active run when leaving the view', async () => {
  const fetch = vi.fn(() => new Promise(() => {}))
  const settings = await form(fetch)
  await settings.trigger('submit')
  const request = fetch.mock.calls[0][1]
  expect(request.signal.aborted).toBe(false)
  wrapper.unmount()
  wrapper = undefined
  expect(request.signal.aborted).toBe(true)
})
