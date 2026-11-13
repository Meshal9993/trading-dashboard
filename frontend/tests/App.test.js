import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import App from '../src/App.vue'
import { candle, status } from './fixtures'

let update
const retry = vi.fn(), stop = vi.fn(), setTimeframe = vi.fn()
vi.mock('../src/services/marketClient', () => ({ createMarketClient: options => { update = options.onChange; return { start: vi.fn(), setTimeframe: (...args) => setTimeframe(...args), retry: (...args) => retry(...args), stop: () => stop() } } }))
vi.mock('../src/components/MarketChart.vue', () => ({ default: { props: ['observations'], template: '<div data-testid="chart">{{ observations.length }} candles</div>' } }))
let wrapper
const render = () => { wrapper = mount(App); return wrapper }
const push = async patch => { update({ observations: [candle()], status: status(), now: Date.parse('2026-01-03T14:00:00Z'), connection: 'connected', loading: false, error: '', ...patch }); await nextTick() }
afterEach(() => wrapper?.unmount())

describe('dashboard states', () => {
  it('starts with a truthful loading state and no fabricated price', () => {
    render()
    expect(wrapper.text()).toContain('Loading market observations')
    expect(wrapper.text()).toContain('No placeholder prices')
    expect(wrapper.text()).toContain('LATEST OBSERVED CLOSE')
    expect(wrapper.text()).not.toContain('105.00')
  })
  it('shows saved candles, OHLCV, provenance, timestamps, and closed/stale state', async () => {
    render(); await push({})
    expect(wrapper.text()).toContain('Market closed · saved observations')
    expect(wrapper.text()).toContain('Yahoo Finance')
    expect(wrapper.text()).toContain('POLLING FALLBACK · DELAYED/POLLED')
    expect(wrapper.text()).toContain('STALE')
    expect(wrapper.text()).toContain('1d 0h')
    expect(wrapper.text()).toContain('Jan 02, 2026')
    expect(wrapper.text()).toContain('105.00')
    expect(wrapper.text()).toContain('1,000')
    expect(wrapper.find('[data-testid="chart"]').text()).toBe('1 candles')
  })
  it.each([
    [{ connection: 'disconnected' }, 'Connection interrupted'],
    [{ status: status({ state: 'unavailable' }) }, 'Yahoo Finance feed unavailable'],
    [{ status: status({ state: 'disabled', market_status: 'unknown' }) }, 'Polling paused'],
    [{ status: status({ market_status: 'open' }) }, 'Stale observations'],
  ])('shows exceptional state while preserving candles', async (patch, text) => {
    render(); await push(patch)
    expect(wrapper.text()).toContain(text)
    expect(wrapper.text()).toContain('105.00')
  })
  it('shows empty data without inventing candles and supports reconnect', async () => {
    render(); await push({ observations: [], status: null, connection: 'disconnected' })
    expect(wrapper.text()).toContain('No saved observations yet')
    expect(wrapper.text()).not.toContain('105.00')
    await wrapper.find('.reconnect-button').trigger('click')
    expect(retry).toHaveBeenCalled()
  })
  it('updates visible close and volume when a revision arrives', async () => {
    render(); await push({})
    await push({ observations: [candle({ revision: 1, close: 109, volume: 2000 })] })
    expect(wrapper.text()).toContain('109.00')
    expect(wrapper.text()).toContain('2,000')
    expect(wrapper.text()).toContain('COVERAGE')
    wrapper.unmount(); wrapper = null
    expect(stop).toHaveBeenCalled()
  })
})


it('shows upstream outage independently from the connected browser and exposes interval selector', async () => {
  render(); await push({ status: status({ upstream_state: 'unavailable', upstream_connected: false, fallback_active: true }) })
  expect(wrapper.text()).toContain('Upstream unavailable · polling fallback')
  expect(wrapper.find('.connection').text()).toBe('Connected')
  await wrapper.get('select[aria-label="Candle timeframe"]').setValue('5m')
  expect(setTimeframe).toHaveBeenCalledWith('5m')
})

it('shows forming candles, unknown volume and observation-based age for streaming', async () => {
  render(); await push({ observations: [candle({ volume: null, is_complete: false })], status: status({ fallback_active: false, market_status: 'open', upstream_state: 'available', upstream_connected: true, latest_event_time: '2026-01-03T13:59:55Z' }) })
  expect(wrapper.text()).toContain('Unknown')
  expect(wrapper.text()).toContain('Forming candle')
  expect(wrapper.text()).toContain('5s')
  expect(wrapper.text()).toContain('WEBSOCKET · DELAY MAY VARY')
})

it('isolates Replay from incoming live updates and restores the live dashboard', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ symbols: [] }) })))
  render(); await push({})
  await wrapper.findAll('.mode-switch button')[1].trigger('click')
  expect(wrapper.get('#chart-title').text()).toContain('Replay')
  expect(wrapper.text()).toContain('REPLAY · RECORDED DATA')
  expect(wrapper.text()).not.toContain('105.00')
  await push({ observations: [candle({ close: 109 })] })
  expect(wrapper.text()).not.toContain('109.00')
  await wrapper.findAll('.mode-switch button')[0].trigger('click')
  expect(wrapper.text()).toContain('109.00')
  vi.unstubAllGlobals()
})
