import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import ReplayControls from '../src/components/ReplayControls.vue'
import { candleState } from './fixtures'

let wrapper
const catalog = { symbols: [{ symbol: 'AAPL', first_receipt: '2026-01-02T14:00:00Z', last_receipt: '2026-01-02T14:01:00Z' }] }
const response = data => ({ ok: true, json: async () => data })
const state = (cursor = 0, complete = false, empty = false, timeframe = '1m') => ({ ...candleState({ timeframe }), mode: 'replay', replay: { cursor, high_water: 10, processed: cursor, total: 2, clock: '2026-01-02T14:00:30Z', complete, empty, message: empty ? 'No recorded observations in this selection.' : null } })
const setup = async (handler = () => response(state())) => {
  vi.stubGlobal('fetch', vi.fn(url => Promise.resolve(url === '/replay/catalog' ? response(catalog) : handler(url))))
  wrapper = mount(ReplayControls)
  await flushPromises()
  return wrapper
}
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('loads a frozen selection, steps by cursor, and restarts at zero', async () => {
  await setup(url => response(state(Number(new URL(url, 'http://localhost').searchParams.get('cursor')) + (url.includes('advance=true') ? 1 : 0))))
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(fetch.mock.calls.at(-1)[0]).toContain('cursor=0&advance=false')
  await wrapper.findAll('button')[2].trigger('click'); await flushPromises()
  expect(fetch.mock.calls.at(-1)[0]).toContain('high_water=10')
  expect(wrapper.emitted('state').at(-1)[0].replay.cursor).toBe(1)
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(fetch.mock.calls.at(-1)[0]).not.toContain('high_water=')
  expect(wrapper.emitted('state').at(-1)[0].replay.cursor).toBe(0)
})

it('plays sequentially, pauses on completion, and recalculates timeframe without advancing', async () => {
  vi.useFakeTimers()
  await setup(url => {
    const p = new URL(url, 'http://localhost').searchParams
    const c = Number(p.get('cursor')) + (p.get('advance') === 'true' ? 1 : 0)
    return response(state(c, c === 2, false, p.get('timeframe')))
  })
  await wrapper.get('button').trigger('click'); await flushPromises()
  await wrapper.findAll('button')[1].trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('Playing')
  await vi.advanceTimersByTimeAsync(300); await flushPromises()
  expect(wrapper.text()).toContain('Finished')
  await wrapper.setProps({ timeframe: '5m' }); await flushPromises()
  expect(fetch.mock.calls.at(-1)[0]).toContain('timeframe=5m&cursor=2&advance=false')
})

it('clearly reports an empty selection without candles', async () => {
  await setup(() => response({ ...state(0, true, true), candles: [], metadata: [] }))
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('No recorded observations in this selection.')
  expect(wrapper.emitted('state').at(-1)[0].observations).toEqual([])
})

it('invalidates an outstanding old range and ignores its response', async () => {
  let resolve
  await setup(() => new Promise(r => { resolve = r }))
  await wrapper.get('button').trigger('click')
  await wrapper.get('[aria-label="Replay start UTC"]').setValue('2026-01-03T14:00:00Z')
  resolve(response(state(1))); await flushPromises()
  expect(wrapper.emitted('state').at(-1)[0].observations).toEqual([])
  expect(wrapper.emitted('state').at(-1)[0].replay).toBeNull()
})

it('reports validation errors and cancels playback on unmount', async () => {
  await setup(() => ({ ok: false, json: async () => ({ detail: 'Replay start must not be after end' }) }))
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('start must not be after end')
})
