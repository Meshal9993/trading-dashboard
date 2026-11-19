<script setup>
import { onBeforeUnmount, reactive, ref } from 'vue'
import EquityCurve from './EquityCurve.vue'
import { DEFAULT_SMA_PERIODS, MAX_SMA_PERIOD, validSmaPeriods } from '../services/sma'

const fields = reactive({
  symbol: 'AAPL',
  start_date: '',
  end_date: '',
  initial_cash: '1000.00',
  short_period: DEFAULT_SMA_PERIODS.short,
  long_period: DEFAULT_SMA_PERIODS.long,
})
const result = ref(null)
const error = ref('')
const busy = ref(false)
let pending

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 6,
})
const sma = new Intl.NumberFormat('en-US', { maximumFractionDigits: 6 })
const money = micros => Number.isFinite(micros) ? currency.format(micros / 1_000_000) : 'Unavailable'
const formatTime = timestamp => timestamp ? timestamp.replace('T', ' ').replace(/(?:Z|\+00:00)$/, '') : '—'
const actionLabel = side => side === 'buy' ? 'Buy' : 'Close position'
const pnlClass = amount => amount === null ? '' : amount < 0 ? 'negative' : amount > 0 ? 'positive' : ''
const amounts = [
  ['Cash', 'cash_micros'],
  ['Average cost / share', 'average_cost_micros'],
  ['Realized P&L', 'realized_micros'],
  ['Unrealized P&L', 'unrealized_micros'],
]

function validationError(cash) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(fields.start_date) || !/^\d{4}-\d{2}-\d{2}$/.test(fields.end_date)) {
    return 'Enter a start date and an end date.'
  }
  if (fields.end_date < fields.start_date) return 'End date must be on or after start date.'
  if (!/^\d+(?:\.\d{1,6})?$/.test(cash) || !Number.isFinite(Number(cash)) || Number(cash) <= 0) {
    return 'Enter positive initial cash with up to six decimal places.'
  }
  if (!validSmaPeriods({ short: fields.short_period, long: fields.long_period })) {
    return `Use whole SMA periods from 1 to ${MAX_SMA_PERIOD}, with a smaller short period.`
  }
  return ''
}

async function run() {
  if (busy.value) return
  result.value = null
  const cash = fields.initial_cash.trim()
  error.value = validationError(cash)
  if (error.value) return

  const request = new AbortController()
  pending = request
  busy.value = true
  try {
    const response = await fetch('/backtest/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...fields, initial_cash: cash }),
      signal: request.signal,
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = data?.detail
      throw new Error(typeof detail === 'string' ? detail : detail?.[0]?.msg ?? 'The backtest request failed.')
    }
    if (!data?.performance || !['signals', 'orders', 'fills', 'equity_curve', 'warnings'].every(key => Array.isArray(data[key]))) {
      throw new Error('The backtest response is incomplete.')
    }
    if (!request.signal.aborted) result.value = data
  } catch (failure) {
    if (!request.signal.aborted) error.value = failure.message || 'The backtest request failed.'
  } finally {
    if (pending === request) {
      pending = null
      busy.value = false
    }
  }
}

onBeforeUnmount(() => pending?.abort())
</script>

<template>
  <div class="backtest">
    <div class="page-heading">
      <div>
        <h1>SMA crossover backtest</h1>
        <p class="subtitle">Simulate a long-only strategy on one-minute candles from saved AAPL quotes.</p>
      </div>
    </div>

    <section class="panel" aria-labelledby="backtest-settings-title">
      <div class="panel-heading">
        <div><h2 id="backtest-settings-title">Backtest settings</h2><p>Dates are inclusive in New York time.</p></div>
      </div>
      <form class="backtest-form" novalidate @submit.prevent="run">
        <fieldset :disabled="busy">
          <label>Symbol
            <select v-model="fields.symbol"><option value="AAPL">AAPL</option></select>
          </label>
          <label>Start date
            <input v-model="fields.start_date" type="date" required />
          </label>
          <label>End date
            <input v-model="fields.end_date" type="date" required />
          </label>
          <label>Initial cash (USD)
            <input v-model="fields.initial_cash" type="text" inputmode="decimal" required />
          </label>
          <label>Short SMA period
            <input v-model.number="fields.short_period" type="number" min="1" :max="MAX_SMA_PERIOD" step="1" required />
          </label>
          <label>Long SMA period
            <input v-model.number="fields.long_period" type="number" min="1" :max="MAX_SMA_PERIOD" step="1" required />
          </label>
        </fieldset>
        <div class="backtest-actions">
          <button class="paper-button" type="submit" :disabled="busy">Run backtest</button>
          <span v-if="busy" role="status">Running backtest…</span>
        </div>
        <p v-if="error" role="alert" class="backtest-error">{{ error }}</p>
      </form>
    </section>

    <template v-if="result">
      <section class="panel backtest-performance" aria-labelledby="backtest-performance-title">
        <div class="panel-heading">
          <div>
            <h2 id="backtest-performance-title">Backtest performance</h2>
            <p>{{ result.symbol }} · {{ result.start_date }} to {{ result.end_date }} · SMA {{ result.short_period }}/{{ result.long_period }}</p>
          </div>
        </div>
        <dl class="backtest-metrics">
          <div>
            <dt>Final equity</dt>
            <dd data-testid="backtest-equity">{{ money(result.performance.equity_micros) }}</dd>
          </div>
          <div>
            <dt>Total P&amp;L</dt>
            <dd :class="pnlClass(result.performance.total_micros)" data-testid="backtest-total">{{ money(result.performance.total_micros) }}</dd>
          </div>
          <div><dt>Starting cash</dt><dd>{{ money(result.initial_cash_micros) }}</dd></div>
          <div><dt>Shares held</dt><dd>{{ result.performance.shares }}</dd></div>
          <div v-for="[label, key] in amounts" :key="key">
            <dt>{{ label }}</dt>
            <dd :class="key.endsWith('realized_micros') ? pnlClass(result.performance[key]) : ''">{{ money(result.performance[key]) }}</dd>
          </div>
        </dl>
        <p class="backtest-context">
          {{ result.completed_candles }} completed {{ result.completed_candles === 1 ? 'candle' : 'candles' }}
          · {{ result.fills.length }} {{ result.fills.length === 1 ? 'fill' : 'fills' }}
          <template v-if="result.as_of"> · Through {{ formatTime(result.as_of) }} UTC</template>
        </p>
        <p v-if="result.performance.price_time" class="backtest-context">
          Final valuation price {{ money(result.performance.price_micros) }} at {{ formatTime(result.performance.price_time) }} UTC.
        </p>
        <p v-if="!result.completed_candles" class="backtest-context" role="status">No completed candles in this range.</p>
        <ul v-if="result.warnings.length" class="backtest-warnings" aria-label="Backtest warnings">
          <li v-for="warning in result.warnings" :key="warning">{{ warning }}</li>
        </ul>
      </section>

      <EquityCurve :points="result.equity_curve" />

      <div class="panel backtest-ledger">
        <div class="table-scroll">
          <table class="paper-history">
            <caption>Backtest fills</caption>
            <thead><tr>
              <th scope="col">Order</th><th scope="col">Action</th><th scope="col">Shares</th>
              <th scope="col">Fill price</th><th scope="col">Source time (UTC)</th>
              <th scope="col">Received (UTC)</th><th scope="col">Arrival</th>
            </tr></thead>
            <tbody>
              <tr v-for="fill in result.fills" :key="fill.order_id">
                <td>#{{ fill.order_id }}</td><td>{{ actionLabel(fill.side) }}</td><td>{{ fill.quantity }}</td>
                <td>{{ money(fill.price_micros) }}</td><td>{{ formatTime(fill.source_time) }}</td>
                <td>{{ formatTime(fill.receipt_time) }}</td><td>#{{ fill.arrival_sequence }}</td>
              </tr>
              <tr v-if="!result.fills.length"><td colspan="7">No simulated fills.</td></tr>
            </tbody>
          </table>
        </div>

        <div class="table-scroll">
          <table class="paper-history">
            <caption>Backtest orders</caption>
            <thead><tr>
              <th scope="col">Order</th><th scope="col">Action</th><th scope="col">Shares</th>
              <th scope="col">Submitted (UTC)</th><th scope="col">Status</th>
            </tr></thead>
            <tbody>
              <tr v-for="order in result.orders" :key="order.id">
                <td>#{{ order.id }}</td><td>{{ actionLabel(order.side) }}</td><td>{{ order.quantity }}</td>
                <td>{{ formatTime(order.submitted_at) }}</td>
                <td class="paper-status"><strong>{{ order.status }}</strong><span v-if="order.rejection_reason">{{ order.rejection_reason }}</span></td>
              </tr>
              <tr v-if="!result.orders.length"><td colspan="5">No orders submitted.</td></tr>
            </tbody>
          </table>
        </div>

        <div class="table-scroll">
          <table class="paper-history">
            <caption>Strategy signals</caption>
            <thead><tr>
              <th scope="col">Crossover</th><th scope="col">Candle (UTC)</th><th scope="col">Decision (UTC)</th>
              <th scope="col">Short SMA</th><th scope="col">Long SMA</th><th scope="col">Order</th>
            </tr></thead>
            <tbody>
              <tr v-for="signal in result.signals" :key="signal.candle_time">
                <td>{{ signal.side === 'up' ? 'Upward' : 'Downward' }}</td>
                <td>{{ formatTime(signal.candle_time) }}</td><td>{{ formatTime(signal.decision_time) }}</td>
                <td>{{ sma.format(signal.short) }}</td><td>{{ sma.format(signal.long) }}</td>
                <td>{{ signal.order_id === null ? '—' : `#${signal.order_id}` }}</td>
              </tr>
              <tr v-if="!result.signals.length"><td colspan="6">No crossover signals.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.backtest { display: grid; gap: 20px; }
.backtest .page-heading { margin-bottom: 0; }
.backtest-form { padding: 0 23px 23px; }
.backtest-form fieldset {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(130px, 100%), 1fr));
  gap: 14px;
  margin: 0;
  padding: 0;
  border: 0;
  min-inline-size: 0;
}
.backtest-form label {
  display: grid;
  gap: 6px;
  color: #aab9ca;
  font-size: .8rem;
}
.backtest-form input, .backtest-form select {
  width: 100%;
  min-width: 0;
  min-height: 36px;
  border: 1px solid #364351;
  border-radius: 5px;
  padding: .4rem .55rem;
  background: #1b2734;
  color: #e3eaf2;
  font: inherit;
  font-variant-numeric: tabular-nums;
}
.backtest-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 16px; font-size: .8rem; }
.backtest-actions button { background: #bcdfb1; color: #142518; font-weight: 650; }
.backtest-error { color: #ee9aa4; margin: .8rem 0 0; font-size: .83rem; }
.backtest-performance { padding-bottom: 18px; }
.backtest-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(135px, 100%), 1fr));
  gap: 14px;
  margin: 0;
  padding: 0 23px;
}
.backtest-metrics > div { background: #141d26; border-radius: 6px; padding: 13px; }
.backtest-metrics dt { color: #aab9ca; font-size: .76rem; }
.backtest-metrics dd { margin: 6px 0 0; font-size: 1.13rem; font-weight: 650; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.backtest-context, .backtest-warnings { margin: 12px 23px 0; font-size: .81rem; line-height: 1.5; color: #aab9ca; }
.backtest-warnings { padding-inline-start: 1.2rem; color: var(--amber); }
.backtest-ledger .table-scroll + .table-scroll { border-top: 1px solid var(--border); }
.backtest-ledger caption { padding-left: 23px; }
@media (max-width: 580px) {
  .backtest-form, .backtest-metrics { padding-inline: 14px; }
  .backtest-context, .backtest-warnings { margin-inline: 14px; }
}
</style>
