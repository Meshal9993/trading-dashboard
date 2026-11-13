import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMarketClient } from '../src/services/marketClient'
import { candle, status, candleState } from './fixtures'

class Socket {
  static instances = []
  constructor(url) { this.url = url; Socket.instances.push(this) }
  close() { this.onclose?.() }
  open() { this.onopen?.() }
  message(data) { this.onmessage?.({ data: JSON.stringify(data) }) }
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() }
let client, states
function setup(fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => candleState() })) {
  states = []
  client = createMarketClient({ onChange: state => states.push(state), location: new URL('https://dashboard.example/'), WebSocketImpl: Socket, fetchImpl })
  client.start()
  return { latest: () => states.at(-1), socket: Socket.instances.at(-1) }
}
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-02T14:02:00Z')); Socket.instances = [] })
afterEach(() => { client?.stop(); vi.useRealTimers() })

describe('market connection', () => {
  it('loads saved observations over HTTP and connects using secure WebSockets', async () => {
    const { latest, socket } = setup()
    await flush()
    expect(socket.url).toBe('wss://dashboard.example/ws/candles?symbol=AAPL&timeframe=1m')
    expect(latest().observations[0].close).toBe(105)
    expect(latest().loading).toBe(false)
    socket.open()
    expect(latest().connection).toBe('connected')
  })
  it('merges snapshots and revisions while ignoring stale revisions', async () => {
    const { socket, latest } = setup()
    socket.message(candleState({ sequence: 2, rows: [candle({ close: 109 })] }))
    await flush()
    socket.message(candleState({ sequence: 1 }))
    expect(latest().observations[0].close).toBe(109)
    socket.message(candleState({ sequence: 3, rows: [candle({ close: 108 })] }))
    expect(latest().observations[0].close).toBe(108)
  })
  it('retains saved data during disconnect, retries, and reconnects with a snapshot', async () => {
    const { socket, latest } = setup()
    await flush()
    socket.open()
    socket.close()
    expect(latest().connection).toBe('disconnected')
    expect(latest().observations).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1000)
    expect(Socket.instances).toHaveLength(2)
    Socket.instances[1].message(candleState({ sequence: 2 }))
    expect(latest().connection).toBe('connected')
    expect(latest().observations[0].close).toBe(105)
  })
  it('handles unavailable backend without making up prices', async () => {
    const { socket, latest } = setup(vi.fn().mockRejectedValue(new Error('offline')))
    await flush()
    socket.close()
    expect(latest().observations).toEqual([])
    expect(latest().loading).toBe(false)
    expect(latest().error).toContain('Cannot retrieve saved')
  })
  it('retains backend feed errors and rejects malformed messages', async () => {
    const { socket, latest } = setup()
    await flush()
    socket.message(candleState({ sequence: 2, feedStatus: status({ state: 'unavailable' }) }))
    expect(latest().status.state).toBe('unavailable')
    socket.onmessage({ data: '{bad json' })
    expect(latest().error).toContain('Invalid feed message')
    expect(latest().observations).toHaveLength(1)
  })
  it('detects a silent connection and advances local age clock', async () => {
    const { socket, latest } = setup()
    socket.open()
    await flush()
    const now = latest().now
    await vi.advanceTimersByTimeAsync(21000)
    expect(latest().now - now).toBe(21000)
    expect(latest().connection).toBe('disconnected')
  })
  it('cleans up timers and ignores messages after unmount', async () => {
    const { socket, latest } = setup()
    await flush()
    client.stop()
    socket.message(candleState({ sequence: 10, rows: [candle({ close: 109 })] }))
    await vi.advanceTimersByTimeAsync(60000)
    expect(Socket.instances).toHaveLength(1)
    expect(latest().observations[0].close).toBe(105)
  })
  it('does not let late HTTP status overwrite newer WebSocket status', async () => {
    let resolve
    const promise = new Promise(done => { resolve = done })
    const { socket, latest } = setup(() => promise)
    socket.message(candleState({ sequence: 2, feedStatus: status({ state: 'unavailable' }) }))
    resolve({ ok: true, json: async () => candleState() })
    await flush()
    expect(latest().status.state).toBe('unavailable')
    expect(latest().observations[0].close).toBe(105)
  })
})


it('switches interval, clears old bars and ignores old socket and HTTP responses', async () => {
  let resolve
  const request = new Promise(done => { resolve = done })
  const { socket, latest } = setup(() => request)
  socket.message(candleState())
  client.setTimeframe('5m')
  expect(latest().observations).toEqual([])
  expect(Socket.instances.at(-1).url).toContain('timeframe=5m')
  socket.message(candleState({ sequence: 99, rows: [candle({ close: 109 })] }))
  resolve({ ok: true, json: async () => candleState() })
  await flush()
  expect(latest().observations).toEqual([])
  Socket.instances.at(-1).message(candleState({ timeframe: '5m' }))
  expect(latest().observations[0].close).toBe(105)
})

it('replaces datasets rather than merging polling and raw prices and preserves unknown volume', async () => {
  const { socket, latest } = setup()
  socket.message(candleState({ sequence: 2, dataset: 'polling' }))
  expect(latest().status.fallback_active).toBe(true)
  socket.message(candleState({ sequence: 3, rows: [candle({ volume: null })] }))
  expect(latest().observations).toHaveLength(1)
  expect(latest().observations[0].volume).toBeNull()
  expect(latest().status.fallback_active).toBe(false)
})
