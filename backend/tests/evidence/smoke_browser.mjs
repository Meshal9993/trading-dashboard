import { spawn } from 'node:child_process'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import assert from 'node:assert/strict'

const [frontendUrl, backendUrl, chromePath, profileDir, outputDir] = process.argv.slice(2)
assert(frontendUrl && backendUrl && chromePath && profileDir && outputDir,
  'Expected frontend URL, backend URL, Chrome executable, profile directory, and evidence directory')
for (const address of [frontendUrl, backendUrl]) {
  const url = new URL(address)
  assert(url.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(url.hostname),
    'The smoke test must use locally hosted fixture services')
}

async function waitFor(label, check, timeout = 12_000) {
  const deadline = Date.now() + timeout
  let lastError
  do {
    try {
      const result = await check()
      if (result) return result
    } catch (error) {
      lastError = error
    }
    await delay(100)
  } while (Date.now() < deadline)
  throw new Error(`Timed out waiting for ${label}${lastError ? `: ${lastError.message}` : ''}`)
}

async function fetchOk(url, options) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(5_000) })
  if (!response.ok) throw new Error(`${url} returned ${response.status}: ${await response.text()}`)
  return response
}

async function openDevTools(address) {
  const socket = new WebSocket(address)
  const pending = new Map()
  const pageExceptions = []
  let commandId = 0
  socket.addEventListener('message', ({ data }) => {
    const message = JSON.parse(data)
    if (message.method === 'Runtime.exceptionThrown') {
      const { exception, text } = message.params.exceptionDetails
      pageExceptions.push(exception?.description ?? text)
    }
    const command = pending.get(message.id)
    if (command) {
      if (message.error) command.reject(new Error(message.error.message))
      else command.resolve(message.result)
    }
  })
  socket.addEventListener('close', () => {
    for (const command of pending.values()) command.reject(new Error('Chrome DevTools disconnected'))
  })
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Chrome DevTools connection timed out')), 5_000)
      socket.addEventListener('open', () => { clearTimeout(timer); resolve() }, { once: true })
      socket.addEventListener('error', () => {
        clearTimeout(timer)
        reject(new Error('Could not connect to Chrome DevTools'))
      }, { once: true })
    })
  } catch (error) {
    socket.close()
    throw error
  }
  return {
    close: () => socket.close(),
    pageExceptions,
    send(method, params = {}) {
      const id = ++commandId
      let timer
      return new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error(`Chrome DevTools timed out in ${method}`)), 5_000)
        pending.set(id, { resolve, reject })
        socket.send(JSON.stringify({ id, method, params }))
      }).finally(() => {
        clearTimeout(timer)
        pending.delete(id)
      })
    },
  }
}

async function stopChrome(chrome, exited) {
  if (chrome.exitCode !== null || chrome.signalCode !== null || chrome.pid === undefined) return
  chrome.kill()
  if (!await Promise.race([exited.then(() => true), delay(2_000, false, { ref: false })])) {
    chrome.kill('SIGKILL')
    await exited
  }
}

await mkdir(outputDir, { recursive: true })
const chrome = spawn(chromePath, [
  '--headless=new', '--disable-background-networking', '--disable-dev-shm-usage', '--no-first-run',
  '--no-default-browser-check', '--no-proxy-server',
  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1',
  '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', `--user-data-dir=${profileDir}`,
  '--window-size=1440,1140', 'about:blank',
], { stdio: ['ignore', 'ignore', 'pipe'] })
let chromeLog = ''
chrome.stderr.on('data', chunk => { chromeLog = (chromeLog + chunk).slice(-3000) })
let launchError
const exited = new Promise(resolve => {
  chrome.once('exit', resolve)
  chrome.once('error', error => { launchError = error; resolve() })
})
let devtools

async function page(callback, ...args) {
  const { result, exceptionDetails } = await devtools.send('Runtime.evaluate', {
    expression: `(${callback.toString()})(...${JSON.stringify(args)})`,
    awaitPromise: true,
    returnByValue: true,
  })
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description ?? exceptionDetails.text)
  return result.value
}

const text = testId => page(id => document.querySelector(`[data-testid="${id}"]`)?.textContent.trim(), testId)
const table = caption => page(name => [...document.querySelectorAll('caption')]
  .find(item => item.textContent.trim() === name)?.parentElement.innerText, caption)
const click = label => waitFor(`enabled ${label} button`, () => page(name => {
  const button = [...document.querySelectorAll('button')]
    .find(item => !item.disabled && item.textContent.trim() === name && item.getClientRects().length)
  if (!button) return false
  button.click()
  return true
}, label))
const setFields = (selector, fields) => page((scope, values) => {
  const container = document.querySelector(scope)
  for (const [label, value] of Object.entries(values)) {
    const input = [...(container?.querySelectorAll('label') ?? [])]
      .find(item => item.textContent.trim() === label)?.querySelector('input')
    if (!input) throw new Error(`Missing ${label} input in ${scope}`)
    input.value = value
    input.dispatchEvent(new Event('input', { bubbles: true }))
  }
}, selector, fields)

async function screenshot(name, selector) {
  if (selector) {
    await page(async target => {
      document.querySelector(target).scrollIntoView({ block: 'start', behavior: 'instant' })
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    }, selector)
  }
  const { data } = await devtools.send('Page.captureScreenshot', { format: 'png' })
  await writeFile(join(outputDir, `${name}.png`), Buffer.from(data, 'base64'))
}

async function checkSma(short, long, shortValue, longValue) {
  await waitFor(`SMA ${short}/${long} to display ${shortValue} and ${longValue}`, () => page((expected) => {
    const chart = document.querySelector('.chart-canvas canvas')
    const values = [...document.querySelectorAll('.sma-legend span')].slice(0, 2).map(item => item.textContent.trim())
    return chart?.getBoundingClientRect().width > 0 && expected.every((value, index) => values[index] === value)
  }, [`Short SMA (${short}) ${shortValue}`, `Long SMA (${long}) ${longValue}`]))
}

function orderStatus(side, status, price = '—') {
  return waitFor(`${side} order ${status} at ${price}`, () => page((expected) => {
    const rows = document.querySelectorAll('.paper-panel > .table-scroll tbody tr')
    return [...rows].some(row => {
      const cells = [...row.cells].map(cell => cell.textContent.trim())
      return cells[1] === expected[0] && cells[3] === expected[1] && cells[4] === expected[2]
    })
  }, [side, status, price]))
}

async function checkPaper(cash, shares, realized, unrealized, total) {
  const fields = {
    'paper-cash': cash, 'paper-shares': shares, 'paper-realized_micros': realized,
    'paper-unrealized_micros': unrealized, 'paper-total_micros': total,
  }
  await waitFor(`paper balances ${JSON.stringify(fields)}`, async () => {
    const actual = await Promise.all(Object.keys(fields).map(text))
    assert.deepEqual(actual, Object.values(fields))
    return true
  })
}

const emitPrice = step => fetchOk(new URL(`/test/smoke/price/${step}`, backendUrl), { method: 'POST' })
const paperIsUnchanged = async () => await text('paper-cash') === '$1,000.00'
  && await text('paper-shares') === '0' && (await table('Order history'))?.includes('No paper orders yet.')

async function backtestResult() {
  await waitFor('backtest results', async () => await text('backtest-equity') === '$988.00')
  const total = await text('backtest-total')
  const fills = await table('Backtest fills')
  const orders = await table('Backtest orders')
  const signals = await table('Strategy signals')
  assert.equal(total, '-$12.00')
  assert(fills?.includes('$17.00') && fills.includes('$5.00') && fills.includes('15:07:04'), fills)
  assert(orders?.includes('15:05:00') && orders.includes('15:07:00'), orders)
  assert(signals?.includes('Upward') && signals.includes('Downward'), signals)
  assert(await page(() => !!document.querySelector('[aria-label="Backtest equity curve, from $1,000.00 to $988.00"]')),
    'Missing backtest equity curve')
  return { total, fills, orders, signals }
}

try {
  const target = await waitFor('a local Chrome page target', async () => {
    if (launchError) throw launchError
    if (chrome.exitCode !== null) throw new Error(chromeLog || `Chrome exited with code ${chrome.exitCode}`)
    const portFile = await readFile(join(profileDir, 'DevToolsActivePort'), 'utf8')
    const [port] = portFile.split('\n')
    const response = await fetchOk(`http://127.0.0.1:${port}/json/list`)
    const targets = await response.json()
    return targets.find(item => item.type === 'page')?.webSocketDebuggerUrl
  })
  devtools = await openDevTools(target)
  await devtools.send('Page.enable')
  await devtools.send('Runtime.enable')
  const { errorText } = await devtools.send('Page.navigate', { url: frontendUrl })
  assert(!errorText, errorText)

  await click('Live dashboard')
  await setFields('.sma-periods', { 'Short SMA period': '2', 'Long SMA period': '3' })
  await click('Apply SMA periods')
  await checkSma(2, 3, '3.00', '4.33')
  await setFields('.sma-periods', { 'Short SMA period': '3', 'Long SMA period': '4' })
  await click('Apply SMA periods')
  await checkSma(3, 4, '4.33', '8.00')
  await screenshot('smoke-sma', '.chart-panel')
  console.log('PASS SMA chart: 2/3 shows $3.00 and $4.33; 3/4 shows $4.33 and $8.00.')

  await waitFor('the initial paper account', paperIsUnchanged)
  await click('Backtest')
  await setFields('.backtest-form', {
    'Start date': '2026-11-09', 'End date': '2026-11-09', 'Initial cash (USD)': '1000.00',
    'Short SMA period': '2', 'Long SMA period': '3',
  })
  await click('Run backtest')
  const firstBacktest = await backtestResult()
  await click('Run backtest')
  assert.deepEqual(await backtestResult(), firstBacktest, 'Backtest rerun changed its results')
  await screenshot('smoke-backtest', '.backtest-performance')
  console.log('PASS Backtest: both runs show upward and downward signals, $17 and $5 next-observation fills, $988 final equity.')

  await click('Live dashboard')
  await waitFor('the unchanged paper account after backtesting', paperIsUnchanged)
  await setFields('.paper-controls', { 'Share quantity': '4' })
  await click('Buy')
  await orderStatus('Buy', 'pending')
  assert.equal(await text('paper-cash'), '$1,000.00')
  await emitPrice('buy-price')
  await orderStatus('Buy', 'filled', '$100.00')
  await checkPaper('$600.00', '4', '$0.00', '$0.00', '$0.00')
  await emitPrice('first-close')
  await checkPaper('$600.00', '4', '$0.00', '$40.00', '$40.00')

  await setFields('.paper-controls', { 'Share quantity': '1' })
  await click('Sell')
  await orderStatus('Sell', 'pending')
  await emitPrice('sell-price')
  await orderStatus('Sell', 'filled', '$110.00')
  await checkPaper('$710.00', '3', '$10.00', '$30.00', '$40.00')
  await emitPrice('second-close')
  await checkPaper('$710.00', '3', '$10.00', '-$15.00', '-$5.00')

  await click('Close Position')
  await orderStatus('Close Position', 'pending')
  await emitPrice('exit-price')
  await orderStatus('Close Position', 'filled', '$105.00')
  await checkPaper('$1,025.00', '0', '$25.00', '$0.00', '$25.00')
  const finalStatuses = await page(() => [...document.querySelectorAll('.paper-panel .paper-status strong')]
    .map(status => status.textContent.trim()))
  assert.deepEqual(finalStatuses, ['filled', 'filled', 'filled'], 'Expected exactly three filled paper orders')
  const pnlHistory = await table('P&L history')
  assert(pnlHistory?.includes('Candle valuation') && pnlHistory.includes('-$5.00') && pnlHistory.includes('$25.00'), pnlHistory)
  await screenshot('smoke-paper-pnl', '.paper-panel')
  console.log('PASS Paper trading and P&L: three orders waited for distinct fixture arrivals; $1,025 cash, 0 shares, $25 realized, $0 unrealized.')

  assert.deepEqual(devtools.pageExceptions, [], 'The browser reported uncaught application errors')
} catch (error) {
  if (devtools) {
    await screenshot('smoke-failure').catch(() => {})
    const alerts = await page(() => [...document.querySelectorAll('[role="alert"]')].map(alert => alert.innerText))
      .catch(() => [])
    if (alerts.length) console.error(`Visible application alerts: ${alerts.join(' | ')}`)
  } else if (chromeLog) {
    console.error(chromeLog)
  }
  throw error
} finally {
  devtools?.close()
  await stopChrome(chrome, exited)
}
