<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { decodeCandleState } from '../services/candles'
import { appendSmaPeriods } from '../services/sma'

const props = defineProps({
  timeframe: { type: String, default: '1m' },
  smaPeriods: Object,
  paperTrading: Boolean,
  suspended: Boolean,
})
const emit = defineEmits(['state'])
const symbols = ref([]), symbol = ref(''), start = ref(''), end = ref('')
const paperEnabled = ref(false)
const replay = ref(null), playing = ref(false), busy = ref(false), error = ref(''), ready = ref(false)
let cursor = 0, highWater, generation = 0, controller, timer, paperAccountId
const emptyState = () => emit('state', { observations: [], timeframe: props.timeframe, connection: 'replay', loading: false, now: Date.now(), status: { state: 'replay', fallback_active: false }, replay: null })
function pause() { playing.value = false; clearTimeout(timer) }
function invalidate() { pause(); generation++; controller?.abort(); cursor = 0; highWater = undefined; paperAccountId = undefined; replay.value = null; ready.value = false; busy.value = false; error.value = ''; emptyState() }
async function frame(advance = false, reset = false) {
  if (!symbol.value || !start.value || !end.value) return
  if (reset) {
    invalidate()
    if (props.paperTrading && paperEnabled.value) paperAccountId = crypto.randomUUID()
    ready.value = true
  }
  const token = ++generation
  controller?.abort(); controller = new AbortController(); busy.value = true; error.value = ''
  try {
    const params = appendSmaPeriods(new URLSearchParams({ symbol: symbol.value, start: start.value, end: end.value,
      timeframe: props.timeframe, cursor: String(cursor), advance: String(advance) }), props.smaPeriods)
    if (highWater !== undefined) params.set('high_water', String(highWater))
    const path = paperAccountId ? `/paper/replay/${paperAccountId}/frame` : '/replay/frame'
    const response = await fetch(`${path}?${params}`, { method: paperAccountId ? 'POST' : 'GET', signal: controller.signal })
    if (!response.ok) { const body = await response.json(); throw new Error(body.detail || 'Replay unavailable') }
    const data = await response.json()
    if (token !== generation) return
    const decoded = decodeCandleState(data, props.timeframe, props.smaPeriods)
    if (data.mode !== 'replay' || !data.replay || (paperAccountId && data.paper_account_id !== paperAccountId)) throw new Error('Invalid replay response')
    replay.value = data.replay; cursor = data.replay.cursor; highWater = data.replay.high_water; ready.value = true
    emit('state', { observations: decoded.observations, sma: decoded.sma, status: decoded.status, timeframe: props.timeframe,
      connection: 'replay', loading: false, error: '', replay: data.replay, now: Date.parse(data.replay.clock),
      lastArrival: decoded.lastArrival, snapshotAsOf: data.as_of, paperAccountId })
    if (data.replay.complete) pause()
    else if (playing.value) timer = setTimeout(() => frame(true), 300)
  } catch (failure) {
    if (token === generation && failure.name !== 'AbortError') { pause(); error.value = failure.message; ready.value = false }
  } finally { if (token === generation) busy.value = false }
}
function play() { playing.value = true; frame(true) }
watch([symbol, start, end, paperEnabled], invalidate)
watch(() => props.suspended, suspended => { if (suspended) pause() })
watch([() => props.timeframe, () => props.smaPeriods], () => {
  pause()
  if (ready.value) frame(false)
  else emptyState()
})
onMounted(async () => {
  emptyState()
  const token = generation
  try {
    const response = await fetch('/replay/catalog')
    if (!response.ok) throw new Error('Cannot load recorded symbols')
    const data = await response.json()
    if (token !== generation) return
    symbols.value = data.symbols
    if (data.symbols.length) {
      symbol.value = data.symbols[0].symbol
      start.value = data.symbols[0].first_receipt
      end.value = data.symbols[0].last_receipt
    }
  } catch (failure) { if (token === generation) error.value = failure.message }
})
onBeforeUnmount(() => { pause(); generation++; controller?.abort() })
</script>

<template>
  <section class="panel replay-controls" aria-label="Replay controls">
    <div><h2>Replay recorded observations</h2><p>Read-only SQLite playback · receipt range in UTC · original arrival order</p></div>
    <div class="replay-fields">
      <label>Saved symbol<select v-model="symbol" aria-label="Saved symbol"><option disabled value="">Select symbol</option><option v-for="item in symbols" :key="item.symbol" :value="item.symbol">{{ item.symbol }}</option></select></label>
      <label>Start receipt (UTC ISO)<input v-model="start" aria-label="Replay start UTC" placeholder="2026-11-10T20:47:00Z" /></label>
      <label>End receipt (UTC ISO)<input v-model="end" aria-label="Replay end UTC" placeholder="2026-11-10T20:53:00Z" /></label>
    </div>
    <label v-if="paperTrading" class="paper-replay-option"><input v-model="paperEnabled" type="checkbox" /> Enable paper trading for this replay</label>
    <div class="replay-actions">
      <button :disabled="!symbol || busy" @click="frame(false, true)">Load / restart replay</button>
      <button :disabled="!ready || busy || replay?.complete" @click="playing ? pause() : play()">{{ playing ? 'Pause replay' : 'Play replay' }}</button>
      <button :disabled="!ready || busy || playing || replay?.complete" @click="frame(true)">Next observation</button>
      <span v-if="busy" role="status">Loading replay…</span>
    </div>
    <p v-if="!symbols.length && !error">No saved raw observations available.</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="replay" class="replay-clock" data-testid="replay-clock">
      <strong>Replay clock · {{ replay.clock }}</strong>
      <span>{{ replay.processed }} / {{ replay.total }} arrivals · {{ replay.complete ? 'Finished' : playing ? 'Playing' : 'Paused' }}</span>
      <span v-if="replay.arrival">Arrival #{{ replay.arrival.arrival_sequence }} · {{ replay.arrival.disposition }} · source {{ replay.arrival.source_time_utc }} · original receipt {{ replay.arrival.receipt_time_utc }}</span>
      <p v-if="replay.empty" role="status">{{ replay.message }}</p>
    </div>
    <p>Pre-range records supply baselines and correction context within affected candles. No observations are written. Playback advances one arrival every 300 ms, plus request time.</p>
  </section>
</template>
