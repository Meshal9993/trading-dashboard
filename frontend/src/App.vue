<script setup>
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import ReplayControls from './components/ReplayControls.vue'
import MarketChart from './components/MarketChart.vue'
import { createMarketClient } from './services/marketClient'
import { ageSeconds, formatAge } from './services/observations'

const liveFeed = shallowRef({ observations: [], status: null, connection: 'connecting', loading: true, error: '', timeframe: '1m', now: Date.now() })
const client = createMarketClient({ onChange: state => { liveFeed.value = state } })
const mode = ref('live')
const replayFeed = shallowRef({ observations: [], timeframe: '1m', connection: 'replay', loading: false, now: Date.now(), status: { state: 'replay', fallback_active: false } })
const feed = computed(() => mode.value === 'replay' ? replayFeed.value : liveFeed.value)
function changeTimeframe(value) {
  hovered.value = null
  if (mode.value === 'replay') replayFeed.value = { ...replayFeed.value, timeframe: value, observations: [], loading: true }
  else client.setTimeframe(value)
}
function changeMode(value) { mode.value = value; hovered.value = null }
const chart = ref(null)
const hovered = ref(null)
const windowSize = ref('recent')
const observations = computed(() => windowSize.value === 'recent' ? feed.value.observations.slice(-390) : feed.value.observations)
const latest = computed(() => feed.value.observations.at(-1))
const selected = computed(() => hovered.value || latest.value)
const status = computed(() => feed.value.status)
const timeframe = computed(() => feed.value.timeframe || '1m')
const timeframeLabel = computed(() => ({ '1m': '1 minute', '5m': '5 minutes', '15m': '15 minutes', '1h': '1 hour', '1d': 'Daily' })[timeframe.value])
const fallback = computed(() => status.value?.fallback_active ?? true)
const age = computed(() => status.value?.latest_event_time
  ? Math.max(0, Math.floor((feed.value.now - Date.parse(status.value.latest_event_time)) / 1000))
  : ageSeconds(feed.value.observations, feed.value.now))
const stale = computed(() => age.value !== null && age.value > 180)
const closed = computed(() => status.value?.market_status === 'closed')
const change = computed(() => {
  const rows = observations.value
  return rows.length ? ((rows.at(-1).close - rows[0].open) / rows[0].open) * 100 : null
})
const connectionLabel = computed(() => mode.value === 'replay' ? 'Replay · offline capable' : ({ connecting: 'Connecting', connected: 'Connected', disconnected: 'Disconnected' })[feed.value.connection])
const feedLabel = computed(() => ({ replay: 'Replay · recorded data', starting: 'Starting', available: 'Available', degraded: 'Degraded', unavailable: 'Unavailable', disabled: 'Paused · saved data', waiting: 'Waiting for quote', connecting: 'Connecting upstream', stopped: 'Stopped' })[status.value?.state] || 'Awaiting status')
const banner = computed(() => {
  if (mode.value === 'replay') return { title: 'Replay · recorded observations', detail: 'Read-only arrival-order playback from SQLite. Source timestamps are preserved; late observations and revisions appear when their receipt is replayed. This is not live data.' }
  if (feed.value.connection === 'disconnected') return { title: 'Connection interrupted', detail: 'Reconnecting automatically. Previously received observations remain visible.' }
  if (status.value?.upstream_state === 'unavailable' && fallback.value) return { title: 'Upstream unavailable · polling fallback', detail: 'Yahoo WebSocket is reconnecting. The browser remains connected; displayed candles come from the separately labeled polling history.' }
  if (status.value?.state === 'unavailable') return { title: 'Yahoo Finance feed unavailable', detail: 'The backend is retrying. Any candles shown are saved observations.' }
  if (closed.value) return { title: 'Market closed · saved observations', detail: 'Showing the latest available regular-session candles. Data age continues to increase while the market is closed.' }
  if (status.value?.state === 'disabled') return { title: 'Polling paused · saved observations', detail: 'External polling is disabled on the backend. Saved candles remain available for review.' }
  if (stale.value) return { title: 'Stale observations', detail: 'The latest source observation is more than 3 minutes old. Prices shown are historical, not current quotes.' }
  if (status.value?.state === 'degraded') return { title: 'Some observations unavailable', detail: 'The backend skipped invalid provider data. Valid candles are shown without filling gaps.' }
  if (fallback.value) return { title: 'Polling fallback · delayed/polled', detail: 'The live upstream has no usable recent regular-session observations. Showing separate Yahoo chart history; it is not real-time.' }
  return { title: 'Streamed observations · not guaranteed real-time', detail: 'Yahoo WebSocket observations are persisted before candle calculation. Quotes can be delayed, volume may be unknown, and candles can be revised.' }
})
const money = value => Number.isFinite(value) ? new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value) : '—'
const integer = value => value === null ? 'Unknown' : Number.isFinite(value) ? new Intl.NumberFormat('en-US').format(value) : '—'
const time = value => value ? new Date(value).toLocaleString('en-US', { timeZone: 'UTC', month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : 'Not available'
onMounted(() => client.start())
onBeforeUnmount(() => client.stop())
</script>

<template>
  <div class="dashboard">
    <header class="topbar">
      <a class="brand" href="#main" aria-label="Market Observatory home"><span class="brand-mark" aria-hidden="true">▥</span><span>MARKET<span class="brand-light"> / OBSERVATORY</span></span></a>
      <div class="header-meta"><span class="assignment">IST 402 <span> / </span> A4.1</span><span class="connection" :class="feed.connection"><i />{{ connectionLabel }}</span></div>
    </header>

    <main id="main">
      <div class="page-heading"><div><p class="eyebrow">MARKET WORKSPACE <span>/</span> EQUITIES</p><h1>A clearer view of the market.</h1><p class="subtitle">Price, volume, and the context behind every observation.</p></div><span class="source-pill">Y! <span>Yahoo Finance</span><span class="pill-separator">|</span><b>{{ mode === 'replay' ? 'REPLAY · RECORDED DATA' : fallback ? 'POLLING FALLBACK · DELAYED/POLLED' : 'WEBSOCKET · DELAY MAY VARY' }}</b></span></div>

      <div class="mode-switch" aria-label="Dashboard mode"><button :aria-pressed="mode === 'live'" @click="changeMode('live')">Live dashboard</button><button :aria-pressed="mode === 'replay'" @click="changeMode('replay')">Replay</button></div>
      <ReplayControls v-if="mode === 'replay'" :timeframe="timeframe" @state="replayFeed = $event" />

      <section class="instrument-panel" aria-label="AAPL overview">
        <div class="instrument"><span class="ticker-icon">A</span><div><div class="ticker-name">AAPL <span>NASDAQ</span></div><p>Apple Inc. <span>·</span> USD</p></div></div>
        <div class="price"><span class="price-caption">LATEST OBSERVED CLOSE</span><div>{{ money(latest?.close) }} <small>USD</small></div><p v-if="change !== null && Number.isFinite(change)" :class="change >= 0 ? 'positive' : 'negative'">{{ change >= 0 ? '+' : '' }}{{ change.toFixed(2) }}% <span>over displayed candles</span></p><p v-else>No observations received</p></div>
        <div class="overview-stat"><span class="stat-label">CANDLE INTERVAL</span><strong>{{ timeframeLabel }}</strong><span>Regular session only</span></div>
        <div class="overview-stat"><span class="stat-label">LATEST CANDLE VOLUME</span><strong>{{ integer(latest?.volume) }}</strong><span>Shares · observed volume</span></div>
      </section>

      <div class="workspace-grid">
        <section class="chart-panel panel" aria-labelledby="chart-title" :data-arrival-sequence="feed.lastArrival?.arrival_sequence" :data-timeframe="timeframe" :data-snapshot-as-of="feed.snapshotAsOf">
          <div class="panel-heading"><div><h2 id="chart-title">{{ mode === 'replay' ? 'Replay' : 'Price & volume' }} <span class="subtle">/ AAPL</span></h2><p>{{ timeframeLabel }} candles <span>·</span> UTC <span>·</span> {{ latest ? latest.is_complete ? 'Complete interval' : 'Forming candle' : 'Awaiting candles' }}</p></div><div class="chart-actions"><select class="timeframe-select" aria-label="Candle timeframe" :value="timeframe" @change="changeTimeframe($event.target.value)"><option value="1m">1m</option><option value="5m">5m</option><option value="15m">15m</option><option value="1h">1h</option><option value="1d">1d</option></select><div class="segmented" aria-label="Observation window"><button :aria-pressed="windowSize === 'recent'" :class="{ active: windowSize === 'recent' }" @click="windowSize = 'recent'; hovered = null">Latest 390</button><button :aria-pressed="windowSize === 'all'" :class="{ active: windowSize === 'all' }" @click="windowSize = 'all'; hovered = null">All received</button></div><button class="icon-button" title="Fit chart to observations" aria-label="Fit chart to observations" @click="chart?.fit()">⤢</button></div></div>
          <div class="ohlc-strip" aria-label="Selected candle"><span v-for="[label, key] of [['O', 'open'], ['H', 'high'], ['L', 'low'], ['C', 'close']]" :key="key">{{ label }} <b>{{ money(selected?.[key]) }}</b></span><span class="volume-legend"><i /> VOL <b>{{ integer(selected?.volume) }}</b></span></div>
          <div class="chart-wrap">
            <MarketChart ref="chart" :observations="observations" :timeframe="timeframe" :mode="mode" @hover="hovered = $event" />
            <div v-if="!observations.length" class="chart-empty" role="status"><span class="empty-icon" aria-hidden="true">⌁</span><h3>{{ mode === 'replay' ? 'Replay · no observations displayed' : feed.loading ? 'Loading market observations' : 'No saved observations yet' }}</h3><p>{{ mode === 'replay' ? feed.replay?.empty ? 'No recorded observations in this selection.' : 'Load a saved selection, then play or step through arrivals.' : feed.loading ? 'Connecting to the backend and retrieving saved candles.' : 'Candles will appear when the backend receives valid Yahoo Finance data.' }}</p><span>No placeholder prices. Only provider observations.</span></div>
          </div>
          <div class="chart-footer"><span><i class="legend-up" /> Up candle <i class="legend-down" /> Down candle</span><span>{{ observations.length }} candles <span>·</span> {{ hovered ? time(hovered.event_time) : 'Scroll to zoom · drag to explore' }}</span></div>
          <div class="notice" role="status"><span class="notice-symbol" aria-hidden="true">ⓘ</span><div><strong>{{ banner.title }}</strong><p>{{ banner.detail }}</p><p v-if="stale && closed && mode !== 'replay'" class="stale-note">Stale / historical · {{ formatAge(age) }} since latest source observation.</p></div></div>
        </section>

        <aside class="sidebar">
          <section class="panel feed-panel" aria-labelledby="feed-title"><div class="panel-heading"><h2 id="feed-title">Feed monitor</h2><span class="small-label">AAPL</span></div><div class="feed-state"><span class="status-dot" :class="status?.state" /><strong>{{ feedLabel }}</strong><span v-if="stale" class="tag">STALE</span></div><dl class="details"><div><dt>Data source</dt><dd>Yahoo Finance</dd></div><div><dt>Delivery</dt><dd>{{ mode === 'replay' ? 'SQLite arrival replay' : fallback ? 'HTTP poll → WebSocket' : 'Yahoo WS → FastAPI WS' }}</dd></div><div><dt>Upstream</dt><dd data-testid="upstream-state">{{ mode === 'replay' ? 'Not required for replay' : status?.upstream_state || 'Unknown' }} · {{ status?.upstream_connected ? 'socket open' : 'socket closed' }}</dd></div><div><dt>Market</dt><dd>{{ closed ? 'Closed' : status?.market_status === 'open' ? 'Open · regular session' : 'Unknown' }}</dd></div><div><dt>Data age</dt><dd class="age" :class="{ 'text-amber': stale }">{{ formatAge(age) }}</dd></div></dl><div class="timestamp"><span>{{ fallback ? 'LATEST POLLED SOURCE TIME · UTC' : 'NEWEST SOURCE OBSERVATION · UTC' }}</span><strong>{{ time(status?.latest_event_time || latest?.event_time) }}</strong></div><div class="timestamp"><span>LATEST SOURCE RECEIPT · UTC</span><strong>{{ time((fallback ? status?.latest_receipt_time : status?.last_upstream_receipt_time || status?.latest_receipt_time) || latest?.receipt_time) }}</strong></div><div class="timestamp"><span>LAST SUCCESSFUL POLL · UTC</span><strong>{{ time(status?.last_successful_poll) }}</strong></div><button v-if="mode !== 'replay'" class="reconnect-button" @click="client.retry()"><span aria-hidden="true">↻</span> Reconnect</button><p v-if="feed.error" class="client-error" role="alert">{{ feed.error }}</p></section>
          <section class="panel provenance"><span class="eyebrow">KNOW YOUR DATA</span><h2>Context before conviction.</h2><p>Raw candles are calculated from stored Yahoo WebSocket observations. Polling fallback is a separate dataset. Neither source guarantees real-time exchange prices.</p><div class="provenance-rule" /><p><b>Age</b> uses the newest received source timestamp for streamed observations, or the latest provider timestamp for polling fallback. Unknown volume is shown explicitly; completion does not guarantee finality.</p><span class="provenance-label">EXPLICIT PROVENANCE · NO REAL-TIME GUARANTEE</span></section>
        </aside>
      </div>

      <section class="panel ledger" aria-labelledby="ledger-title"><div class="panel-heading"><div><h2 id="ledger-title">Observation ledger</h2><p>Most recent derived candles · UTC</p></div><span class="small-label">LAST 5 OBSERVATIONS</span></div><div class="table-scroll"><table><caption class="sr-only">Latest AAPL candles, including coverage and completion status</caption><thead><tr><th>EVENT TIME · UTC</th><th>OPEN</th><th>HIGH</th><th>LOW</th><th>CLOSE</th><th>VOLUME</th><th>COVERAGE</th><th>INTERVAL</th></tr></thead><tbody><tr v-for="item in feed.observations.slice(-5).toReversed()" :key="item.observation_id"><td>{{ time(item.event_time) }}</td><td>{{ money(item.open) }}</td><td>{{ money(item.high) }}</td><td>{{ money(item.low) }}</td><td :class="item.close >= item.open ? 'positive' : 'negative'">{{ money(item.close) }}</td><td>{{ integer(item.volume) }}</td><td>{{ item.coverage ? `${item.coverage.available_minutes}/${item.coverage.expected_minutes} min` : 'Not measured' }}</td><td><span class="interval-tag">{{ item.is_complete ? 'Complete*' : 'Forming' }}</span></td></tr><tr v-if="!feed.observations.length"><td colspan="8" class="empty-row">{{ feed.loading ? 'Retrieving observations…' : 'No observations available. The ledger never uses simulated prices.' }}</td></tr></tbody></table></div><p class="table-note">*Interval elapsed is inferred; provider corrections can still arrive.</p></section>
    </main>
    <footer class="page-footer"><span>MARKET OBSERVATORY <span> / </span> IST 402 · Assignment 4.1</span><span>TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc. · <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">TradingView</a></span></footer>
  </div>
</template>
