import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import PaperAccount from '../src/components/PaperAccount.vue'

const response = data => ({ ok: true, json: async () => data })
const snapshot = (cash, shares, orders = []) => ({ cash_micros: cash * 1_000_000, shares, orders })
const row = (side, quantity, status, price = null, reason = null) => ({
  id: 1, side, quantity, status, price_micros: price === null ? null : price * 1_000_000,
  rejection_reason: reason, source_time_utc: price === null ? null : '2026-11-09T15:31:00Z',
})

let wrapper
afterEach(() => {
  wrapper?.unmount()
  vi.unstubAllGlobals()
})

function mockAccount(...results) {
  const fetchMock = vi.fn()
  for (const result of results) fetchMock.mockResolvedValueOnce(response(result))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function button(name) {
  return wrapper.findAll('button').find(item => item.text() === name)
}

it('submits a whole-share buy and refreshes pending and filled balance from the server', async () => {
  const fetchMock = mockAccount(snapshot(1000, 0), snapshot(1000, 0, [row('buy', 4, 'pending')]),
    snapshot(600, 4, [row('buy', 4, 'filled', 100)]))
  wrapper = mount(PaperAccount, { props: { accountId: 'live' } })
  await flushPromises()
  await wrapper.find('input').setValue(4)
  await button('Buy').trigger('click')
  await flushPromises()
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ side: 'buy', quantity: 4 })
  expect(wrapper.text()).toContain('pending')
  expect(wrapper.find('[data-testid=paper-cash]').text()).toBe('$1,000.00')
  await wrapper.setProps({ arrivalSequence: 1 })
  await flushPromises()
  expect(wrapper.find('[data-testid=paper-cash]').text()).toBe('$600.00')
  expect(wrapper.find('[data-testid=paper-shares]').text()).toBe('4')
  expect(wrapper.text()).toContain('filled')
  expect(wrapper.text()).toContain('$100.00')
})

it('validates quantity, displays an insufficient-position rejection, and omits quantity on close', async () => {
  const fetchMock = mockAccount(snapshot(1000, 0),
    snapshot(1000, 0, [row('sell', 1, 'rejected', null, 'Insufficient shares')]),
    snapshot(1000, 0, [row('close', 0, 'rejected', null, 'Insufficient shares')]))
  wrapper = mount(PaperAccount, { props: { accountId: 'live' } })
  await flushPromises()
  await wrapper.find('input').setValue(1.5)
  await button('Buy').trigger('click')
  expect(wrapper.get('[role=alert]').text()).toContain('whole share')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  await wrapper.find('input').setValue(1)
  await button('Sell').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('Insufficient shares')
  await button('Close Position').trigger('click')
  await flushPromises()
  expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({
    side: 'close', request_id: expect.any(String),
  })
})

it('retries the identical request body when the first submission response is lost', async () => {
  const fetchMock = mockAccount(snapshot(1000, 0))
  fetchMock.mockRejectedValueOnce(new Error('connection reset'))
  fetchMock.mockResolvedValueOnce(response(snapshot(1000, 0, [row('buy', 1, 'pending')])))
  wrapper = mount(PaperAccount, { props: { accountId: 'live' } })
  await flushPromises()
  await button('Buy').trigger('click')
  await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('uncertain')
  expect(button('Buy').element.disabled).toBe(true)
  await button('Retry order request').trigger('click')
  await flushPromises()
  expect(fetchMock.mock.calls[2][1].body).toBe(fetchMock.mock.calls[1][1].body)
  expect(wrapper.text()).toContain('pending')
  expect(button('Retry order request')).toBeUndefined()
})

it('uses a distinct paper account in replay and suppresses the live balance while switching', async () => {
  const fetchMock = mockAccount(snapshot(600, 4), snapshot(1000, 0))
  wrapper = mount(PaperAccount, { props: { accountId: 'live' } })
  await flushPromises()
  await wrapper.setProps({ accountId: null })
  expect(wrapper.text()).not.toContain('$600.00')
  expect(button('Buy')).toBeUndefined()
  await wrapper.setProps({ accountId: 'replay-account' })
  await flushPromises()
  expect(fetchMock.mock.calls[1][0]).toBe('/paper/accounts/replay-account')
  expect(wrapper.find('[data-testid=paper-cash]').text()).toBe('$1,000.00')
})
