# Prompt 4 · Display a candlestick chart in Vue

~~~text
Read README.md and AGENTS.md. Use TradingView Lightweight Charts to display
one-minute candles and trading volume in the Vue frontend.

CHECK
If GET /api/history exists, use the sample candles it returns. Otherwise,
use these two synthetic one-minute candles to check the chart.

timestamp,open,high,low,close,volume
2026-11-09T14:30:00Z,100,103,99,102,1200
2026-11-09T14:31:00Z,102,104,100,101,850

Inspect the project instructions, operating system, frontend manifest,
lockfile, package manager, and installed packages for lightweight-charts.

TAKE ACTION
Reuse the installed chart library when available. If it is missing, identify
a compatible version, explain why it is needed, and give the exact install
command, frontend target, and affected dependency files. Wait for my approval
before installing it or changing a dependency declaration or lockfile.

Draw one candlestick per timestamp and align its volume bar underneath. In
the Vue chart adapter, convert the UTC timestamp to the library's time value
for both series. Label the data as a synthetic one-minute sample. Let the
reader inspect a candle's date, time, prices, and volume. Include the
TradingView attribution notice and link.
https://tradingview.github.io/lightweight-charts/docs

VERIFY
Check that the frontend resolves the chart package and builds. Open the
running application in a browser. Confirm that the two adjacent minutes appear
as separate candles. Inspect 14:31 UTC on November 9, 2026 and compare the
displayed close (101) and volume (850 shares) with the source row.
Record the browser observation in docs/verification.md.
~~~
