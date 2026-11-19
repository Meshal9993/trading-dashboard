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

it('changes SMA periods at the current replay cursor without advancing or reopening the range', async () => {
  await setup(url => {
    const params = new URL(url, 'http://localhost').searchParams
    const cursor = Number(params.get('cursor')) + (params.get('advance') === 'true' ? 1 : 0)
    return response(state(cursor))
  })
  await wrapper.get('button').trigger('click'); await flushPromises()
  await wrapper.findAll('button')[2].trigger('click'); await flushPromises()
  await wrapper.setProps({ smaPeriods: { short: 2, long: 3 } }); await flushPromises()
  const params = new URL(fetch.mock.calls.at(-1)[0], 'http://localhost').searchParams
  expect(Object.fromEntries(params)).toMatchObject({
    cursor: '1', advance: 'false', high_water: '10', sma_short: '2', sma_long: '3',
  })
  expect(wrapper.emitted('state').at(-1)[0].replay.processed).toBe(1)
})

it('keeps paper replay opt-in and advances a dedicated account without changing it on timeframe refresh', async () => {
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/replay/catalog') return response(catalog)
    const parsed = new URL(url, 'http://localhost')
    const cursor = Number(parsed.searchParams.get('cursor')) + (parsed.searchParams.get('advance') === 'true' ? 1 : 0)
    const id = parsed.pathname.split('/')[3]
    return response({ ...state(cursor, false, false, parsed.searchParams.get('timeframe')), paper_account_id: id })
  }))
  wrapper = mount(ReplayControls, { props: { paperTrading: true } })
  await flushPromises()
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(fetch.mock.calls.at(-1)[0]).toMatch(/^\/replay\/frame\?/)
  expect(fetch.mock.calls.at(-1)[1].method).toBe('GET')

  await wrapper.get('input[type=checkbox]').setValue(true)
  await wrapper.get('button').trigger('click'); await flushPromises()
  const id = wrapper.emitted('state').at(-1)[0].paperAccountId
  expect(id).toMatch(/^[a-f\d-]{36}$/)
  expect(fetch.mock.calls.at(-1)[1].method).toBe('POST')
  await wrapper.findAll('button')[2].trigger('click'); await flushPromises()
  expect(wrapper.emitted('state').at(-1)[0].replay.cursor).toBe(1)
  await wrapper.setProps({ timeframe: '5m' }); await flushPromises()
  const request = fetch.mock.calls.at(-1)[0]
  expect(request).toContain(`/paper/replay/${id}/frame?`)
  expect(request).toContain('timeframe=5m&cursor=1&advance=false')
})

it('pauses replay without resetting the selection when another dashboard mode covers it', async () => {
  vi.useFakeTimers()
  await setup(url => {
    const query = new URL(url, 'http://localhost').searchParams
    return response(state(Number(query.get('cursor')) + (query.get('advance') === 'true' ? 1 : 0)))
  })
  await wrapper.get('button').trigger('click'); await flushPromises()
  await wrapper.findAll('button')[1].trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('1 / 2 arrivals · Playing')
  await wrapper.setProps({ suspended: true })
  await vi.advanceTimersByTimeAsync(300); await flushPromises()
  expect(wrapper.text()).toContain('1 / 2 arrivals · Paused')
  await wrapper.setProps({ suspended: false })
  await wrapper.findAll('button')[2].trigger('click'); await flushPromises()
  expect(wrapper.emitted('state').at(-1)[0].replay.cursor).toBe(2)
})
