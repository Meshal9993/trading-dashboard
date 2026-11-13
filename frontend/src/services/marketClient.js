import { decodeCandleState } from './candles'

export function createMarketClient({ onChange, location = window.location, WebSocketImpl = WebSocket,
  fetchImpl = fetch, clock = () => Date.now() }) {
  let state = { observations: [], status: null, timeframe: '1m', connection: 'connecting', loading: true, error: '', now: clock() }
  let socket, retryTimer, ticker, controller, stopped = false, attempts = 0, generation = 0
  let lastMessage = clock(), latestSequence = -1, latestAsOf = -Infinity
  const emit = patch => { state = { ...state, ...patch, now: clock() }; onChange(state) }

  function apply(data, currentGeneration) {
    if (stopped || currentGeneration !== generation) return
    const decoded = decodeCandleState(data, state.timeframe)
    if (decoded.sequence < latestSequence || (decoded.sequence === latestSequence && decoded.asOf < latestAsOf)) return
    latestSequence = decoded.sequence
    latestAsOf = decoded.asOf
    const same = JSON.stringify(decoded.observations) === JSON.stringify(state.observations)
    emit({ observations: same ? state.observations : decoded.observations, status: decoded.status,
      dataset: decoded.dataset, lastArrival: decoded.lastArrival, snapshotAsOf: data.as_of, loading: false, error: '' })
  }

  async function savedSnapshot(currentGeneration, timeframe) {
    controller?.abort()
    controller = new AbortController()
    try {
      const query = new URLSearchParams({ symbol: 'AAPL', timeframe, dataset: 'auto' })
      const response = await fetchImpl(`/history?${query}`, { signal: controller.signal })
      if (!response.ok) throw new Error('Saved candles unavailable')
      apply(await response.json(), currentGeneration)
    } catch (error) {
      if (!stopped && currentGeneration === generation && error.name !== 'AbortError' && state.loading) {
        emit({ loading: false, error: 'Cannot retrieve saved candles. Reconnecting to the backend.' })
      }
    }
  }

  function connect() {
    if (stopped) return
    const currentGeneration = ++generation
    latestSequence = -1
    latestAsOf = -Infinity
    emit({ connection: 'connecting' })
    lastMessage = clock()
    savedSnapshot(currentGeneration, state.timeframe)
    const url = new URL('/ws/candles', location.href)
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    url.search = new URLSearchParams({ symbol: 'AAPL', timeframe: state.timeframe }).toString()
    try {
      socket = new WebSocketImpl(url.href)
    } catch {
      reconnect(currentGeneration)
      return
    }
    socket.onopen = () => { if (!stopped && currentGeneration === generation) emit({ connection: 'connected' }) }
    socket.onmessage = event => {
      if (stopped || currentGeneration !== generation) return
      try {
        const data = JSON.parse(event.data)
        if (!['snapshot', 'candles'].includes(data.type)) throw new Error('Unknown message')
        apply(data, currentGeneration)
        lastMessage = clock()
        attempts = 0
        emit({ connection: 'connected' })
      } catch {
        emit({ error: 'Invalid feed message received. Waiting for valid backend data.' })
      }
    }
    socket.onclose = () => reconnect(currentGeneration)
    socket.onerror = () => {
      if (stopped || currentGeneration !== generation) return
      socket.close()
      reconnect(currentGeneration)
    }
  }

  function reconnect(currentGeneration) {
    if (stopped || currentGeneration !== generation || retryTimer) return
    emit({ connection: 'disconnected' })
    const delay = Math.min(30000, 1000 * 2 ** Math.min(attempts++, 5))
    retryTimer = setTimeout(() => { retryTimer = null; connect() }, delay)
  }

  function retry() {
    generation++
    clearTimeout(retryTimer)
    retryTimer = null
    socket?.close()
    attempts = 0
    connect()
  }

  return {
    start() {
      connect()
      ticker = setInterval(() => {
        emit({})
        if (clock() - lastMessage > 20000 && state.connection !== 'disconnected') {
          socket?.close()
          reconnect(generation)
        }
      }, 1000)
    },
    setTimeframe(timeframe) {
      if (!['1m', '5m', '15m', '1h', '1d'].includes(timeframe)) throw new Error('Invalid timeframe')
      if (state.timeframe === timeframe) return
      emit({ timeframe, observations: [], loading: true, error: '' })
      retry()
    },
    retry,
    stop() {
      stopped = true
      generation++
      clearTimeout(retryTimer)
      clearInterval(ticker)
      controller?.abort()
      socket?.close()
    },
  }
}
