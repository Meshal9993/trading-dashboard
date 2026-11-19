# Prompt 08: application data pipeline

The production lifespan owns one AAPL Yahoo WebSocket subscription and one free
HTTP chart poller per server process. Run a single Uvicorn worker. Both start
independently of browser connections; both are cancelled and awaited on shutdown.
`YAHOO_STREAM_ENABLED=false` disables the subscription; `YAHOO_FEED_ENABLED=false`
disables polling. Disable both for fully offline cached operation.

## Durable observation to visible candle

1. `YahooQuoteConnection` subscribes through pinned yfinance 0.2.66. Its isolated
   adapter uses that release's `_ws.recv()` and `_decode_message` rather than the
   library's unbounded listen/reconnect loop. Upgrading yfinance requires checking
   this adapter. Open timeout is 15 seconds, receive inactivity timeout 60 seconds,
   close timeout 5 seconds; reconnect backoff starts at 2 seconds and caps at 30.
2. Normalize symbol, price and source epoch **milliseconds**; stamp backend receipt
   in UTC. Record the original decoded payload through `RawObservationRepository`.
   The SQLite transaction commits before subscribers are notified. Exact duplicates
   retain arrival journal entries; explicit corrections retain prior versions.
   Same-time differing messages are not automatically assumed to be corrections.
3. `HistoryService` calculates from committed raw observations at a captured clock.
   One-minute candles use source-time ordering, valid cumulative-volume baselines,
   and unknown volume where attribution cannot be established. Larger intervals
   use the same minutes and calendar/session alignment from Prompt 07A. Completion
   means elapsed interval, not immutable or necessarily fully covered data.
4. `/ws/candles?symbol=AAPL&timeframe=1m` sends an initial `snapshot` then `candles`
   states. Supported frames: `1m`, `5m`, `15m`, `1h`, `1d`. Dirty notifications are
   coalesced in a bounded queue; each response is authoritative for its dataset,
   with at most 2,000 bars. A five-second heartbeat recalculates age/completion.
   This is current chart state delivery, not Prompt 09 arrival-order replay.
5. Vue loads `/history?symbol=AAPL&timeframe=...&dataset=auto` and connects to the
   matching same-origin socket. A generation guard rejects old timeframe callbacks;
   sequence/as-of guards reject older HTTP responses within a connection. The
   chart updates its last bar in place, appends a new bar, and replaces series when
   an earlier bar is revised. Switching timeframe clears bars and reloads history.

Candle objects retain exactly `timestamp`, `open`, `high`, `low`, `close`, `volume`.
The envelope separately carries symbol, source, dataset, timeframe, UTC `as_of`,
sequence, per-candle coverage/completion metadata, feed status and last committed
arrival trace. Unknown volume is `null`; the volume histogram uses whitespace,
never an invented zero. The ledger and headline explicitly display Unknown.

## Availability and fallback

Browser socket state and Yahoo upstream socket/availability are separate UI fields.
A connected upstream is only available after a valid observation. Raw data is
preferred while available and its newest source observation is no older than
180 seconds. Otherwise the separately persisted polling dataset is selected; if
that has no bars, saved raw history remains visible with its actual status/age.
Datasets replace one another, never merge. Recovery resubscribes and selects raw
again after a fresh observation. Gaps during an outage cannot be reconstructed as
unreceived raw trades; coverage and unknown-volume metadata remain necessary.

The poller remains at 30 seconds by default. Yahoo sometimes appends an
instantaneous non-minute quote row to its chart response. That row stays in the
legacy SQLite history but is excluded from minute aggregation, rather than rounded
into a fabricated one-minute candle. The polling source age can reference this
latest provider row, whereas displayed bars contain only aligned minutes.

The UI says “WebSocket · delay may vary” or “Polling fallback · delayed/polled”.
Neither mode guarantees exchange real-time prices. Raw age uses the newest source
observation timestamp, not the current candle's interval start. Closed-market,
stale, loading, unavailable and browser-disconnected views retain provenance.
The regular-session calendar is not a real-time exchange-status API.

## Verification boundary

See `docs/verification.md` and `docs/evidence/part4-1/prompt08/`. Genuine Yahoo
quotes are distinguished from constructed unit fixtures and the test-only upstream
failure adapter. The adapter lives under backend tests, uses a local control file,
and adds no production control routes. No fake price source is shipped. Prompt 09
scheduled arrival-order replay remains outside this implementation.

# Prompt 09: read-only arrival-order replay

Replay is a separate dashboard mode using saved raw Yahoo observations, not the
polling candle dataset. The existing live browser client may stay connected in the
background, but its updates cannot replace replay state. Switching back restores
the live dashboard. The backend live subscription/polling lifecycle is unchanged.

## Selection and journal boundary

`GET /replay/catalog` lists supported saved symbols and their earliest/latest backend
receipt timestamps and arrival counts. Only AAPL is supported by the current
calculator/calendar; no other saved symbol is falsely offered. The controls select
an inclusive UTC **receipt-time** range, deliberately not a source-time range.
Late source observations received inside that range belong to the selection.

`GET /replay/frame?symbol=AAPL&start=<aware UTC>&end=<aware UTC>&timeframe=1m`
returns the next selected arrival and a derived chart state. `cursor=0&advance=false`
loads an empty initial state; `advance=true` steps to the next durable
`arrival_sequence`. The first response freezes `high_water`; subsequent requests
carry it so records arriving after the run begins cannot enter playback. Cursor
values must belong to the selection. No server replay session or database write is
created. Endpoint errors use 422 for invalid selections, 503 for database errors.

Only the journal prefix through the current cursor is fed to the same
`HistoryService`, `form_one_minute_candles` and `aggregate_minutes` functions used
by live mode. Equal receipt timestamps never reveal a later arrival early. Earlier
records provide correction-chain context and cumulative-volume baselines, including
quotes before the selected range. Displayed intervals are restricted to those
touched by selected arrivals already revealed; their OHLC includes earlier known
observations within the same interval. The receipt range does not slice an OHLC
bar into a new artificial candle. Source timestamps, correction IDs, duplicates,
and earlier versions are preserved. Duplicates advance the replay trace without
adding volume. Explicit revisions replace their target only at their receipt step.

## Clocks and playback controls

The envelope adds `mode=replay` and `replay` metadata: cursor, high_water, processed,
total, complete, empty, clock and exact current arrival/source/receipt/revision
trace. The calculation/replay clock is the maximum receipt time in the available
prefix, so a recorded backward system-clock adjustment cannot hide records already
received. The original current receipt time is shown separately. Source timestamps
do not become arrival ordering keys, and future-source observations remain subject
to the same calculator clock guard as live mode.

Vue shows “Replay” in the chart title, provenance pill and header. It supports
Load/restart, Play/Pause, Next observation and all five existing timeframes. Playback
steps one recorded arrival every 300 ms plus request time; it preserves ordering,
not original wall-clock spacing. Timeframe changes pause playback and recalculate
the same cursor without advancing. Range changes invalidate prior state and pending
responses. Abort/generation guards prevent stale responses crossing selections;
unmount stops timers and aborts pending frame fetches. Empty selections explicitly
say “No recorded observations in this selection.” Replay needs the local backend
and SQLite, but no Yahoo connection. Replay endpoints are ordinary production
read-only APIs, not test-control endpoints. Vite forwards `/replay` on the same origin.

## Limits

A completed playback can end within a still-forming candle: the clock freezes at
the last selected receipt, not today's wall clock or an invented end tick. Unknown
volume stays null. This first implementation reads and recalculates a journal
prefix per step; large recordings will need indexed range/prefix loading or cached
incremental calculation. No seek/speed controls, multi-symbol calendar support,
trade-complete reconstruction or original interarrival-delay scheduling is claimed.
Recording can continue independently while replay runs; to verify unchanged record
counts across runs, disable both Yahoo workers as documented in verification.

# Prompt 09A: simple moving averages and crossovers

`/history`, `/ws/candles`, and `/replay/frame` accept the optional pair
`sma_short` and `sma_long`. Both are integers from 1 through 2,000 and the
short window must be smaller. Requests without this pair retain their existing
response format. Opted-in responses add an `sma` envelope containing the two
periods and timestamped points with `short`, `long`, and `crossover` values.
The six OHLCV candle fields stay the same.

The calculation uses the selected symbol, timeframe, dataset, and snapshot
clock. Each SMA includes the current completed candle and the preceding
completed closes in its window. Unfinished candles and warm-up windows produce
null values. A missing interval contributes no row and consumes no window slot.
Volume does not affect an SMA. The first pair of unequal averages establishes
the side. Equality retains the previous side, and only a side change emits
`up` or `down`. Python compares the averages before converting them to
display values.

The backend recalculates each authoritative snapshot, including earlier
candles affected by a late observation or revision. Replay passes only the
arrival prefix available at the current cursor. Revising a candle can change
later averages and markers after the revision becomes visible. Frontend
period changes request fresh indicators for the active timeframe and replay
position. The chart draws the server values and signals through Lightweight
Charts, using timestamp alignment with the candlestick series.

Window coverage is bounded by the candles supplied in each response. Partial
interval coverage and elapsed completion remain separate. SMA signals describe
the observed closes available in that dataset.

## Prompt 09C · Simulated order execution

SQLite creates a persistent AAPL-only long account with $1,000. Buy and Sell
accept positive whole-share quantities up to 1,000,000. Orders enter a queue
without assuming a future price. Sales larger than the current position are
rejected immediately. A buy with positive cash can remain pending until its
cost is known. The shared Python `execute_paper_order` function processes
eligible orders in submission order and rechecks available cash and shares
against each fill. No shares or cash are reserved while an order is pending.
Close Position determines the entire remaining position at execution. Orders
fill in full at the observed quote price with zero fees or are rejected.

Live fills take place in the same write transaction that records a newly
received eligible raw quote. The quote must follow submission in the receipt
journal and have a later backend receipt time. Duplicates, explicit revisions,
out-of-order source times, future-dated source times, invalid prices, and
polling candles cannot trigger fills. Distinct quotes with the same source
timestamp can be eligible. A pending order waits until an eligible quote
arrives and can survive shutdown. SQLite stores cash and prices as integer
millionths of a dollar. Raw quote prices are rounded half up to that precision.

Paper replay is opt-in. A separate account is bound to its receipt range,
frozen journal high-water mark, and durable cursor. Each advance reveals one
saved observation. The same execution function takes that observation only
after the cursor passes the order's submission boundary. Repeating a step or
changing timeframe/SMA settings at the same cursor cannot cause another fill.
The original observation receipt time is recorded alongside the simulated
execution time. Chart-only replay continues through the read-only API.

`GET /paper/accounts/{account_id}` returns balances and order history.
`POST /paper/accounts/{account_id}/orders` accepts a UUID request ID, action,
and quantity for Buy or Sell. Each account has a unique request-ID constraint.
An identical retry returns the current saved state. A conflicting reuse returns
409. `POST /paper/replay/{account_id}/frame` starts the isolated account and
advances its saved range. The account is separate from the live-feed simulation.

The `paper_accounts`, `paper_orders`, and `paper_fills` tables persist balances,
queue state, and immutable fills. Each fill has one order ID and a foreign key
to the exact observation arrival. A failed write rolls back the fill, order
status, and balances together. In live mode it also rolls back the triggering
receipt so a committed observation and its execution cannot disagree.

## Prompt 09D · Paper valuation

Each fill appends a `paper_pnl_history` row in the same SQLite transaction as its
cash, share, and order updates. Buys add their cost to the position's total cost
basis. A sale removes the sold shares' proportion of that basis. The remaining
shares keep the same weighted average, and the sale realizes quantity times the
difference between fill price and average cost. The basis is stored as an exact
rational number of millionths of a dollar. Reported amounts are rounded to
millionths only after calculation.

For the zero-fee account, cash plus remaining basis minus starting cash equals
cumulative realized P&L. Unrealized P&L is open shares times valuation price minus
remaining basis. Total is the sum of the exact components, rounded once. A flat
position has zero unrealized P&L regardless of price.

The backend checks for valuations before processing fills at a new receipt and
every two seconds in live mode, so a quiet minute can complete without another
quote arriving. In paper replay the receipt-prefix cursor and replay clock fence
the calculation. A valuation uses the close of a completed regular-session
AAPL one-minute candle derived from raw WebSocket observations. An eligible
minute finishes after the latest fill's market receipt while the account has
shares. The first recorded close for that minute is retained. Later corrections
can update the chart without changing the historical valuation. Once a newer
minute has been valued, a late older minute does not roll the valuation back.
The chart's HTTP polling history remains a separate data source. The latest
saved quote or candle mark is usable for open-position P&L for 180 seconds on the
account's market clock. Unrealized and total P&L are null when it is unavailable.

History rows identify the account and AAPL symbol, fill order or candle start,
event time, price and price time, and quote or candle source. Event keys are
unique per account. Repeated requests and repeated candle checks leave a single
row for each event. Startup reconstructs fill entries from existing 09C fills
and preserves account balances. Historical candle marks start with 09D because
their original valuation times were not recorded. The existing account endpoint
returns the current P&L and historical rows along with orders and balances.

## SMA crossover backtesting

`POST /backtest/run` reads one snapshot of the raw SQLite receipt journal and
returns the results of a fresh in-memory account. It does not run the market
workers, submit orders to the live or replay account, or write tables. Input
dates are inclusive in America/New_York. The current price source is recorded
AAPL WebSocket quotes within the regular exchange session. The endpoint returns
the latest receipt clock it used, completed candle count, signals, orders,
fills, final performance, equity points, and warnings.

### Decision and fill ordering

- Process receipts by durable arrival sequence. The simulation clock never
  moves backward. The end date excludes receipts at or after the next local
  midnight. The run stops at the last recorded receipt inside the range.
- Each one-minute candle freezes when the clock reaches its end. The closing
  quote is the last available source timestamp in that minute, with original
  arrival order breaking ties. A correction can replace a quote while both
  its old and new minute are still open, keeping the original tie position.
  The boundary is processed before receipts at that exact time. Late quotes
  and revisions to frozen candles cannot alter past or later signals.
- Both SMA windows count observed completed closes. Missing intervals consume
  no slot, create no synthetic observations, and do not reset the windows.
  Windows warm up from the selected range. The chart and backtest share the
  same exact-arithmetic SMA state and crossover rule.
- A crossover up queues one whole-share buy when flat. A crossover down
  queues a close of the held shares. One order may wait at a time. Record
  other crossovers without queuing a second order while one is pending.
- A fill requires a distinct new regular-session quote with a recorded receipt
  strictly after the signal and source time at or after the signal. Duplicate,
  revised, out-of-order, or future-dated quotes cannot fill. The rolling
  source-time check allows distinct quotes with an equal source timestamp.
  Closed candles and chart fallback data supply no execution prices.

### Position and equity

The backtest reuses paper trading's long-only balance checks, exact weighted
cost basis, and P&L formulas with the requested starting cash. It fills at the
observed price with zero fees or slippage. An unaffordable buy is rejected
without a fill. Cash and execution prices are in integer millionths of a
dollar, and the cost basis remains exact until reporting.

Equity is cash plus shares times the last completed close or fill price.
Equity snapshots are taken at completed candles and fills. Reports preserve
a pending last order and mark an open position without forcing a sale.
Historical valuation uses the last recorded mark and labels its timestamp.
It does not apply the live account's wall-clock staleness threshold.

## Part 4.2 offline verification

The GitHub Actions workflow starts a fresh Linux job on every push and pull
request. Backend tests receive a temporary SQLite path and run with both
Yahoo workers disabled. The same job runs the Vue tests, builds the production
bundle, and invokes the local browser smoke command. Its evidence upload runs
even if a check fails.

The smoke runner creates a separate SQLite fixture with a recorded-arrival
sequence and launches FastAPI and Vue on loopback ports. Before Chrome opens
the dashboard, the runner verifies the same per-run token directly from
FastAPI and through Vue's API proxy. Test-only routes release fixed quotes and
advance the fixture clock after the browser checks that an order is pending.
The production app has no fixture-control routes. Chrome has its own
temporary profile.

The browser exercises the chart, settings, orders, and performance through
the regular UI. Afterward, a read-only SQLite check compares the original
fixture observations, fills, and valuation history with expected values. The
runner stops the child process groups, removes the fixture and browser profile,
and compares file fingerprints for the saved database and SQLite sidecars.
Logs and screenshots remain in the run's artifact directory.
