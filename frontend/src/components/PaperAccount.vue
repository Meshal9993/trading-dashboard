<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import PaperPerformance from './PaperPerformance.vue'

const props = defineProps({
  accountId: { type: String, default: null },
  arrivalSequence: Number,
})
const account = ref(null)
const quantity = ref(1)
const busy = ref(false)
const error = ref('')
const unresolved = new Map()
const retry = ref(null)
let timer
let generation = 0
let readVersion = 0

const endpoint = id => `/paper/accounts/${encodeURIComponent(id)}`
const currency = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 6,
})
const money = micros => currency.format(micros / 1_000_000)
const hasPending = computed(() => account.value?.orders.some(order => order.status === 'pending'))
const blocked = computed(() => !account.value || busy.value || !!retry.value)

async function readAccount(response) {
  const snapshot = await response.json()
  if (!Number.isFinite(snapshot?.cash_micros) || !Number.isInteger(snapshot.shares) ||
      !Array.isArray(snapshot.orders)) {
    throw new Error('Invalid paper account response')
  }
  return snapshot
}

async function refresh() {
  const id = props.accountId
  if (!id || busy.value) return
  const version = ++readVersion
  try {
    const response = await fetch(endpoint(id))
    if (!response.ok) throw new Error('Paper account unavailable')
    const latest = await readAccount(response)
    if (version === readVersion) {
      account.value = latest
      if (!retry.value) error.value = ''
    }
  } catch {
    if (version === readVersion && !account.value) error.value = 'Could not load the paper account.'
  }
}

async function submit(side) {
  if (blocked.value) return
  const shares = quantity.value
  if (side !== 'close' && (!Number.isInteger(shares) || shares < 1 || shares > 1_000_000)) {
    error.value = 'Enter a whole share quantity from 1 to 1,000,000.'
    return
  }
  const request = { request_id: crypto.randomUUID(), side }
  if (side !== 'close') request.quantity = shares
  await send(request)
}

async function send(request) {
  if (!request || busy.value || !props.accountId) return
  const id = props.accountId
  const current = generation
  ++readVersion
  unresolved.set(id, request)
  busy.value = true
  error.value = ''
  try {
    const response = await fetch(`${endpoint(id)}/orders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    })
    if (response.ok) {
      const latest = await readAccount(response)
      unresolved.delete(id)
      if (current === generation) {
        account.value = latest
        retry.value = null
      }
    } else if (response.status >= 400 && response.status < 500) {
      unresolved.delete(id)
      if (current === generation) {
        retry.value = null
        error.value = 'The server rejected the order request.'
      }
    } else {
      throw new Error('Paper order response unavailable')
    }
  } catch {
    if (current === generation) {
      retry.value = request
      error.value = 'Order status is uncertain. Retry the same request to check it.'
    }
  } finally {
    if (current === generation) busy.value = false
  }
}

watch(() => props.accountId, id => {
  ++generation
  ++readVersion
  account.value = null
  busy.value = false
  retry.value = unresolved.get(id) ?? null
  error.value = ''
  refresh()
}, { immediate: true })
watch(() => props.arrivalSequence, refresh)

onMounted(() => {
  timer = setInterval(() => { if (hasPending.value || account.value?.shares > 0) refresh() }, 1000)
})
onBeforeUnmount(() => {
  ++generation
  ++readVersion
  clearInterval(timer)
})
</script>

<template>
  <section class="panel paper-panel" aria-labelledby="paper-title">
    <div class="panel-heading">
      <div>
        <h2 id="paper-title">Paper trading</h2>
        <p>{{ accountId === 'live' ? 'Live-feed simulation' : 'Replay simulation' }} · No real money</p>
      </div>
      <button v-if="accountId" type="button" class="paper-button" :disabled="busy" @click="refresh">Refresh account</button>
    </div>
    <div v-if="account" class="paper-content">
      <dl class="paper-balances">
        <div><dt>Cash balance</dt><dd data-testid="paper-cash">{{ money(account.cash_micros) }}</dd></div>
        <div><dt>AAPL shares held</dt><dd data-testid="paper-shares">{{ account.shares }}</dd></div>
      </dl>
      <div class="paper-controls">
        <label>Share quantity <input v-model.number="quantity" type="number" step="1" min="1" max="1000000" /></label>
        <button class="paper-button" type="button" :disabled="blocked" @click="submit('buy')">Buy</button>
        <button class="paper-button" type="button" :disabled="blocked" @click="submit('sell')">Sell</button>
        <button class="paper-button" type="button" :disabled="blocked" @click="submit('close')">Close Position</button>
      </div>
      <p class="paper-note">{{ accountId === 'live'
        ? 'Orders wait for the next eligible AAPL WebSocket quote.'
        : 'Advance replay to reveal the next eligible recorded AAPL quote.' }} No fees.</p>
      <button v-if="retry" class="paper-button" type="button" :disabled="busy" @click="send(retry)">Retry order request</button>
    </div>
    <p v-else class="paper-content">{{ accountId ? 'Loading paper account…' : 'Enable paper trading and load a replay range to open a simulated account.' }}</p>
    <p v-if="error" class="paper-error" role="alert">{{ error }}</p>
    <PaperPerformance v-if="account?.pnl" :pnl="account.pnl" />
    <div v-if="account" class="table-scroll">
      <table class="paper-history">
        <caption>Order history</caption>
        <thead><tr><th scope="col">Order</th><th scope="col">Action</th><th scope="col">Shares</th><th scope="col">Status</th><th scope="col">Fill price</th><th scope="col">Observed at (UTC)</th></tr></thead>
        <tbody>
          <tr v-for="order in account.orders" :key="order.id">
            <td>#{{ order.id }}</td>
            <td>{{ { buy: 'Buy', sell: 'Sell', close: 'Close Position' }[order.side] }}</td>
            <td>{{ order.quantity }}</td>
            <td class="paper-status"><strong>{{ order.status }}</strong><span v-if="order.rejection_reason">{{ order.rejection_reason }}</span></td>
            <td>{{ order.price_micros === null ? '—' : money(order.price_micros) }}</td>
            <td>{{ order.source_time_utc ?? '—' }}</td>
          </tr>
          <tr v-if="!account.orders.length"><td colspan="6">No paper orders yet.</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
