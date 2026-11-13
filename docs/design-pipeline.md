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
