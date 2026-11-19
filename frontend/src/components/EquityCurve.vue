<script setup>
import { computed, ref } from 'vue'

const props = defineProps({ points: { type: Array, required: true } })
const showData = ref(false)
const width = 1000
const height = 190
const inset = 10
const currency = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 6,
})
const money = micros => currency.format(micros / 1_000_000)
const time = timestamp => timestamp ? timestamp.replace('T', ' ').replace(/(?:Z|\+00:00)$/, '') : '—'
const plot = computed(() => {
  const data = props.points.flatMap(point => {
    const timestamp = Date.parse(point.timestamp)
    return Number.isFinite(point.equity_micros) && Number.isFinite(timestamp) ? [{ ...point, time: timestamp }] : []
  })
  if (!data.length) return null
  let min = data[0].equity_micros, max = min
  for (const point of data) {
    min = Math.min(min, point.equity_micros)
    max = Math.max(max, point.equity_micros)
  }
  const first = data[0], last = data.at(-1)
  const span = last.time - first.time
  const coordinates = data.map((point, index) => ({
    x: inset + (width - 2 * inset) * (span > 0 ? (point.time - first.time) / span : index / Math.max(1, data.length - 1)),
    y: max === min ? height / 2 : height - inset - (height - 2 * inset) * (point.equity_micros - min) / (max - min),
  }))
  return { min, max, first, last, coordinates, data }
})
</script>

<template>
  <section class="panel equity-panel" aria-labelledby="equity-title">
    <div class="panel-heading"><div><h2 id="equity-title">Equity curve</h2><p>Cash plus the marked value of open shares.</p></div></div>
    <template v-if="plot">
      <div class="equity-plot">
        <div class="equity-axis" aria-hidden="true">
          <span v-if="plot.max !== plot.min">{{ money(plot.max) }}</span>
          <span>{{ money(plot.min) }}</span>
        </div>
        <svg
          :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none"
          role="img" :aria-label="`Backtest equity curve, from ${money(plot.first.equity_micros)} to ${money(plot.last.equity_micros)}`"
        >
          <line v-for="y in [inset, height / 2, height - inset]" :key="y" x1="0" :x2="width" :y1="y" :y2="y" class="equity-grid" />
          <polyline v-if="plot.coordinates.length > 1" :points="plot.coordinates.map(({ x, y }) => `${x},${y}`).join(' ')" class="equity-line" />
          <circle :cx="plot.coordinates.at(-1).x" :cy="plot.coordinates.at(-1).y" r="4" class="equity-dot" />
        </svg>
      </div>
      <div class="equity-times">
        <span>{{ time(plot.first.timestamp) }} UTC</span>
        <span v-if="plot.first.timestamp !== plot.last.timestamp">{{ time(plot.last.timestamp) }} UTC</span>
      </div>
      <details class="equity-data" @toggle="showData = $event.currentTarget.open">
        <summary>Equity data</summary>
        <div v-if="showData" class="table-scroll">
          <table class="paper-history">
            <caption class="sr-only">Account equity by time</caption>
            <thead><tr><th scope="col">Time (UTC)</th><th scope="col">Equity</th></tr></thead>
            <tbody>
              <tr v-for="(point, index) in plot.data" :key="index">
                <td>{{ time(point.timestamp) }}</td><td>{{ money(point.equity_micros) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
    </template>
    <p v-else class="equity-empty">No equity points for this range.</p>
  </section>
</template>

<style scoped>
.equity-plot { display: flex; align-items: stretch; gap: 12px; padding: 0 23px; }
.equity-axis { display: flex; flex-direction: column; justify-content: space-between; color: #aab9ca; padding-block: 4px; }
.equity-axis:has(span:only-child) { justify-content: center; }
.equity-axis, .equity-times { font-size: .72rem; font-variant-numeric: tabular-nums; }
.equity-plot svg { width: 100%; min-width: 0; height: clamp(125px, 20vw, 190px); }
.equity-grid { stroke: #263543; stroke-width: 1px; vector-effect: non-scaling-stroke; }
.equity-line { stroke: #bcdfb1; stroke-width: 2px; fill: none; vector-effect: non-scaling-stroke; }
.equity-dot { fill: #bcdfb1; }
.equity-times { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; color: #aab9ca; padding: 7px 23px 16px; }
.equity-data { padding: 0 23px 18px; font-size: .8rem; }
.equity-data summary { cursor: pointer; }
.equity-data .table-scroll { max-height: 240px; margin-top: 8px; }
.equity-empty { padding: 0 23px 18px; margin: 0; color: #aab9ca; font-size: .8rem; }
@media (max-width: 580px) {
  .equity-plot, .equity-times, .equity-data, .equity-empty { padding-inline: 14px; }
}
</style>
