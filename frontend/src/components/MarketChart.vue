<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CandlestickSeries, ColorType, createChart, HistogramSeries } from 'lightweight-charts'
import { chartData } from '../services/observations'

const props = defineProps({ mode: { type: String, default: 'live' }, observations: { type: Array, default: () => [] }, timeframe: { type: String, default: '1m' } })
const emit = defineEmits(['hover'])
const container = ref(null)
let chart, candles, volume, observer, previous = [], hasData = false

function render() {
  if (!chart) return
  const data = chartData(props.observations)
  const candle = item => ({ time: item.time, open: item.open, high: item.high, low: item.low, close: item.close })
  const bar = item => item.volume === null ? { time: item.time } : ({ time: item.time, value: item.volume, color: item.close >= item.open ? '#2ec4a066' : '#ee758166' })
  const prefixUnchanged = previous.length && data.length >= previous.length &&
    previous.slice(0, -1).every((item, index) => data[index]?.time === item.time && ['open', 'high', 'low', 'close', 'volume'].every(key => data[index]?.[key] === item[key]))
  if (prefixUnchanged && data[previous.length - 1]?.time === previous.at(-1).time) {
    for (const item of data.slice(previous.length - 1)) {
      candles.update(candle(item))
      volume.update(bar(item))
    }
  } else {
    // Older corrections and out-of-order inserts require replacing the sorted data.
    const range = hasData ? chart.timeScale().getVisibleLogicalRange() : null
    candles.setData(data.map(candle))
    volume.setData(data.map(bar))
    if (range) chart.timeScale().setVisibleLogicalRange(range)
  }
  if (!hasData && data.length) chart.timeScale().fitContent()
  hasData = data.length > 0
  previous = data
}

onMounted(() => {
  chart = createChart(container.value, {
    autoSize: false, width: container.value.clientWidth, height: container.value.clientHeight,
    layout: { background: { type: ColorType.Solid, color: '#101720' }, textColor: '#8493a5', fontFamily: 'system-ui, sans-serif', attributionLogo: true },
    grid: { vertLines: { color: '#1b2632' }, horzLines: { color: '#1b2632' } },
    rightPriceScale: { borderColor: '#25303d' },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#25303d', rightOffset: 4 },
    localization: { locale: 'en-US', timeFormatter: time => new Date(Number(time) * 1000).toLocaleString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) },
    crosshair: { mode: 0 },
  })
  candles = chart.addSeries(CandlestickSeries, { upColor: '#2ec4a0', downColor: '#ee7581', borderVisible: false, wickUpColor: '#2ec4a0', wickDownColor: '#ee7581' })
  candles.priceScale().applyOptions({ scaleMargins: { top: 0.12, bottom: 0.06 } })
  volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false }, 1)
  volume.priceScale().applyOptions({ scaleMargins: { top: 0.12, bottom: 0 }, visible: true })
  chart.panes()[1].setHeight(90)
  chart.subscribeCrosshairMove(event => emit('hover', previous.find(item => item.time === event.time) || null))
  observer = new ResizeObserver(() => {
    chart?.applyOptions({ width: container.value.clientWidth, height: container.value.clientHeight })
    if (hasData) chart?.timeScale().fitContent()
  })
  observer.observe(container.value)
  render()
})
watch(() => props.observations, render)
watch(() => props.timeframe, () => { previous = []; hasData = false; render() })
onBeforeUnmount(() => { observer?.disconnect(); chart?.remove() })
defineExpose({ fit: () => chart?.timeScale().fitContent() })
</script>

<template><div ref="container" class="chart-canvas" role="img" :aria-label="`${mode === 'replay' ? 'Replay · ' : ''}AAPL ${timeframe} OHLC candlesticks and trading volume, Yahoo Finance observations. Unknown volume is not drawn. Times in UTC.`" /></template>
