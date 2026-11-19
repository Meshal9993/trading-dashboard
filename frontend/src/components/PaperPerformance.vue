<script setup>
defineProps({ pnl: { type: Object, required: true } })

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 6,
})
const money = micros => micros == null ? 'Unavailable' : currency.format(micros / 1_000_000)
const priceSource = source => ({
  yahoo_finance_websocket_quote: 'Yahoo WebSocket quote',
  yahoo_finance_websocket_1m_close: 'Completed 1m candle close',
})[source] ?? 'Unavailable'
const fields = [
  ['Average cost / share', 'average_cost_micros'],
  ['Cumulative realized P&L', 'realized_micros'],
  ['Unrealized P&L', 'unrealized_micros'],
  ['Total P&L', 'total_micros'],
]
</script>

<template>
  <section class="paper-performance" aria-labelledby="paper-performance-title">
    <div class="paper-content">
      <h3 id="paper-performance-title">Paper performance</h3>
      <dl class="paper-balances">
        <div v-for="[label, key] in fields" :key="key">
          <dt>{{ label }}</dt>
          <dd :data-testid="`paper-${key}`">
            {{ key === 'average_cost_micros' && pnl.current[key] === null ? '—' : money(pnl.current[key]) }}
          </dd>
        </div>
      </dl>
      <p v-if="pnl.current.valuation_status === 'unavailable'" class="paper-note" role="status">
        Valuation unavailable · waiting for a usable price.
      </p>
      <p v-else-if="pnl.current.price_time" class="paper-note">
        {{ priceSource(pnl.current.price_source) }} · {{ money(pnl.current.price_micros) }} · {{ pnl.current.price_time }}
      </p>
      <p v-else class="paper-note">No open position.</p>
    </div>
    <div class="table-scroll">
      <table class="paper-history">
        <caption>P&amp;L history</caption>
        <thead><tr>
          <th scope="col">Event</th><th scope="col">Recorded (UTC)</th><th scope="col">Price and source</th>
          <th scope="col">Cash</th><th scope="col">Shares</th><th scope="col">Realized</th>
          <th scope="col">Unrealized</th><th scope="col">Total</th>
        </tr></thead>
        <tbody>
          <tr v-for="entry in pnl.history" :key="entry.id">
            <td>{{ entry.event === 'fill' ? `Fill #${entry.order_id}` : 'Candle valuation' }}</td>
            <td>{{ entry.event_time }}</td>
            <td>{{ money(entry.price_micros) }} · {{ priceSource(entry.price_source) }}<br />{{ entry.price_time }}</td>
            <td>{{ money(entry.cash_micros) }}</td>
            <td>{{ entry.shares }}</td>
            <td>{{ money(entry.realized_micros) }}</td>
            <td>{{ money(entry.unrealized_micros) }}</td>
            <td>{{ money(entry.total_micros) }}</td>
          </tr>
          <tr v-if="!pnl.history.length"><td colspan="8">No P&amp;L entries yet.</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
