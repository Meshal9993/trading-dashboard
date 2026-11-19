<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries, LineSeries } from 'lightweight-charts'
import { chartData } from '../services/observations'

const props = defineProps({
  mode: { type: String, default: 'live' },
  observations: { type: Array, default: () => [] },
  sma: Object,
  timeframe: { type: String, default: '1m' },
})
const emit = defineEmits(['hover'])
const container = ref(null)
let chart, candles, volume, observer, previous = [], hasData = false
let shortSma, longSma, crossoverMarkers

function renderSma() {
  if (!chart) return
  const visibleTimes = new Set(props.observations.map(candle => candle.event_time))
  const points = (props.sma?.points ?? []).filter(point => visibleTimes.has(point.timestamp))
  const time = point => Date.parse(point.timestamp) / 1000
  const line = field => points.map(point => point[field] === null
    ? { time: time(point) }
    : { time: time(point), value: point[field] })
  shortSma.setData(line('short'))
  longSma.setData(line('long'))
  crossoverMarkers.setMarkers(points.filter(point => point.crossover !== null).map(point => {
    const up = point.crossover === 'up'
    return {
      time: time(point), position: up ? 'belowBar' : 'aboveBar', shape: up ? 'arrowUp' : 'arrowDown',
      color: up ? '#2ec4a0' : '#ee7581', text: up ? 'SMA cross up' : 'SMA cross down',
    }
  }))
}

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
  const smaStyle = { lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false }
  shortSma = chart.addSeries(LineSeries, { ...smaStyle, color: '#83c9fc' })
  longSma = chart.addSeries(LineSeries, { ...smaStyle, color: '#f4bf70' })
  crossoverMarkers = createSeriesMarkers(candles, [])
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
  renderSma()
})
watch(() => props.observations, render)
watch([() => props.observations, () => props.sma], renderSma)
watch(() => props.timeframe, () => { previous = []; hasData = false; render() })
onBeforeUnmount(() => { observer?.disconnect(); crossoverMarkers?.detach(); chart?.remove() })
defineExpose({ fit: () => chart?.timeScale().fitContent() })
</script>

<template><div ref="container" class="chart-canvas" role="img" :aria-label="`${mode === 'replay' ? 'Replay · ' : ''}AAPL ${timeframe} OHLC candlesticks and trading volume, Yahoo Finance observations. Unknown volume is not drawn. Times in UTC.`" /></template>
