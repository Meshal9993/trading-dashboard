// Run with a CUA browser tab at the dashboard served against backtest_server.py's fixture.
function assert(condition, message) {
  if (!condition) throw new Error(message)
}

export async function verifyBacktestBrowser(tab, captureResults) {
  const text = testId => tab.playwright.getByTestId(testId).innerText({ timeoutMs: 5000 })
  const table = name => tab.playwright.getByRole('table', { name, exact: true }).innerText({ timeoutMs: 5000 })
  const paperBefore = [await text('paper-cash'), await text('paper-shares'), await table('Order history')]
  await tab.playwright.getByRole('button', { name: 'Backtest', exact: true }).click()
  const settings = tab.playwright.getByRole('region', { name: 'Backtest settings' })
  const run = () => settings.getByRole('button', { name: 'Run backtest', exact: true }).click()
  await run()
  assert((await settings.getByRole('alert').innerText()).includes('start date'), 'Missing date validation')
  const fill = (name, value) => settings.getByLabel(name, { exact: true }).fill(value)
  await fill('Start date', '2026-11-09')
  await fill('End date', '2026-11-09')
  await fill('Initial cash (USD)', '1000.00')
  await fill('Short SMA period', '2')
  await fill('Long SMA period', '3')

  async function observeResult() {
    await tab.playwright.getByTestId('backtest-equity').waitFor({ state: 'visible', timeoutMs: 8000 })
    const equity = await text('backtest-equity')
    const total = await text('backtest-total')
    const fills = await table('Backtest fills')
    const orders = await table('Backtest orders')
    const signals = await table('Strategy signals')
    assert(equity === '$988.00' && total === '-$12.00', 'Unexpected account performance')
    assert(fills.includes('$17.00') && fills.includes('$5.00') && fills.includes('15:07:04'), 'Missing next-quote fills')
    assert(orders.includes('15:05:00') && orders.includes('15:07:00'), 'Missing decision timestamps')
    assert(signals.includes('Upward') && signals.includes('Downward'), 'Missing crossover direction')
    assert(await tab.playwright.getByRole('img', { name: 'Backtest equity curve, from $1,000.00 to $988.00' }).isVisible(), 'Missing equity curve')
    return { equity, total, fills, orders, signals }
  }

  await run()
  const first = await observeResult()
  await run()
  const second = await observeResult()
  assert(JSON.stringify(first) === JSON.stringify(second), 'Rerun changed the backtest result')
  await captureResults()

  await fill('Start date', '2026-11-10')
  await fill('End date', '2026-11-10')
  await run()
  await tab.playwright.getByText('No completed candles in this range.', { exact: true }).waitFor({ state: 'visible' })
  assert(await text('backtest-equity') === '$1,000.00', 'An empty range changed the starting account')

  await tab.playwright.getByRole('button', { name: 'Live dashboard', exact: true }).click()
  const paperAfter = [await text('paper-cash'), await text('paper-shares'), await table('Order history')]
  assert(JSON.stringify(paperBefore) === JSON.stringify(paperAfter), 'Backtest changed live paper balances or orders')
  return { result: 'PASS', fixture: 'Constructed AAPL quote arrivals; both Yahoo workers disabled.',
    first, second, emptyRange: 'Visible no-data message, starting equity preserved', paperBefore, paperAfter }
}
