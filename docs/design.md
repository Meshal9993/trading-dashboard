# Trading dashboard design — Step 5

## Scope and lifecycle

The backend uses FastAPI and Python's built-in sqlite3 driver. `create_app()`
constructs an isolated application and database configuration; the FastAPI
lifespan initializes SQLite before requests are served. Importing the module
does not create a database. A new connection is opened and closed for each
operation. Startup errors propagate, preventing a partially initialized service.

`GET /health` queries the observations table, returning HTTP 200 with
`{"status":"ok","database":"ok"}` on success or HTTP 503 with a generic error
when SQLite is unavailable. It checks read access, not a full integrity scan or
write readiness. Feed availability is reported separately from database health.
The lifespan also restores cached observations, starts one polling task, and
cancels the task and closes the HTTP client at shutdown. Network and database
work do not block WebSocket delivery; SQLite operations run in worker threads.

## Schema

| Column | SQLite type | Meaning / constraint |
| --- | --- | --- |
| symbol | TEXT | Nonblank instrument identifier |
| source | TEXT | Nonblank provider/feed identifier |
| observation_id | TEXT | Nonblank source candle identifier |
| event_time | TEXT | Candle event time; fixed-width UTC ISO 8601 |
| receipt_time | TEXT | First local receipt of this revision; UTC ISO 8601 |
| open | REAL | Nonnegative opening price |
| high | REAL | Nonnegative high, at least open and close |
| low | REAL | Nonnegative low, at most open and close |
| close | REAL | Nonnegative closing price |
| volume | INTEGER | Nonnegative integer |
| revision | INTEGER | Nonnegative source version |
| is_complete | INTEGER | Boolean: 0 provisional, 1 complete |

Primary key: `(symbol, source, observation_id, revision)`.
Index: `idx_observations_event(symbol, source, event_time)`.
View: `current_market_observations` returns each identity's maximum revision.
The authoritative DDL is `backend/app/db/database.py`.

The source adapter must assign a stable observation ID that distinguishes candle
intervals and sessions. The Yahoo chart adapter uses `AAPL:1m:<epoch_seconds>`
with source `yahoo_finance_chart` and generates local revisions for corrections.
The generic repository still supports explicitly supplied revision numbers.
IDs are scoped by symbol and source. Identifiers are trimmed and case-sensitive.
A given identity must keep its event time across revisions. Interval and session
metadata are not separate columns at this stage.

Pydantic rejects blank identifiers, naive timestamps, nonfinite or negative
prices, inconsistent OHLC ranges, negative or noninteger volume/revision, and
nonboolean completion flags. Volume and revision are bounded to SQLite's signed
64-bit integer range. Timestamps normalize to UTC before persistence;
receipt time is supplied by the caller and must represent local receipt, rather
than provider event time. The Yahoo adapter captures receipt after the HTTP
response body arrives, before parsing, and preserves Yahoo epoch timestamps as
UTC candle-start event times without substituting receipt time. No event-before-receipt assumption is imposed because
of clock skew. SQL also enforces required fields, identity uniqueness, OHLC
bounds, integer counts, and boolean completion. Timestamp and cross-revision
identity validation live in the repository/model; direct SQL bypasses those rules.
REAL prices are suitable for observation display and analysis, not exact monetary
accounting.

## Duplicate, revision, and ordering policy

`ObservationRepository.store()` returns:

| Result | Condition | Effect |
| --- | --- | --- |
| inserted | First revision seen for an identity | Store the revision |
| duplicate | Same identity, revision, and payload, excluding receipt time | No write; retain first receipt |
| revised | Revision higher than the current maximum | Append revision; view selects it |
| stale | Previously unseen revision lower than the maximum | Append history; current stays unchanged |

Reusing a revision with different prices, volume, event time, or completion raises
`ObservationConflict` and rolls back. Changing the event time of an existing
identity also raises a conflict, even for a new revision. A higher revision is
authoritative, including its completion flag; completed candles may be corrected.
Conflicts require the source adapter to resolve or issue a new valid revision.

Each write uses `BEGIN IMMEDIATE` before reading prior revisions. SQLite serializes
writers, so concurrent replays cannot race between comparison and insertion.
The connection waits up to 10 seconds for locks; lock errors propagate to callers.
All SQL values use bound parameters. Connections commit on success and roll back
on exceptions.

Different candles can arrive in any order. `current(symbol, source)` returns their
highest revisions ordered by event time and then observation ID, never receipt
time. For example, receiving revision 2, then revision 0, then revision 1 retains
all three but keeps revision 2 current. Replaying revision 2 adds no row.

## Startup and validation

From the repository root:

```sh
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
python -m uvicorn backend.app.main:app --reload
python -m pytest backend/tests -q
```

The default database is `backend/data/market.sqlite3`. Set `DATABASE_PATH` to use
a different file. Database files and virtual environments are ignored by Git.
Initialization is idempotent and preserves data; schema migrations are deferred.
Tests cover startup, restart preservation, configuration, health failure,
duplicates, conflicts, late candles, revision ordering, source/symbol separation,
timezones, validation, SQL constraints, and concurrent writers with temporary data.

## Yahoo polling and local revisions

The backend requests the free, unofficial Yahoo chart endpoint:
`https://query1.finance.yahoo.com/v8/finance/chart/AAPL`, with `interval=1m`,
`range=5d`, and `includePrePost=false`. An HTTPX async client uses a 15-second
request timeout. No API key, paid service, or subscription is used. Yahoo may
rate-limit or change this endpoint; access is not guaranteed.

The parser aligns timestamp and OHLCV arrays and validates each candle. Source
symbol and granularity must match AAPL/1m. Null prices/volume, impossible OHLC,
future timestamps, and invalid rows are skipped with a count; all-invalid or empty
responses fail. Missing arrays, duplicate timestamps, API errors, HTTP failures,
and invalid JSON fail the poll. Missing volume is never fabricated as zero.
Bars are already provider-aggregated candles; no tick-volume reconstruction is
performed. `is_complete` means the one-minute interval has elapsed at receipt;
it is explicitly inferred and does not guarantee that Yahoo will never revise it.

Yahoo's chart response has no revision sequence. `store_snapshots()` compares
payloads excluding receipt time and incoming revision, under `BEGIN IMMEDIATE`.
An unchanged replay does nothing; a changed payload gets the maximum stored
revision plus one (starting at zero). The entire fetched batch commits atomically
before broadcast. The counter survives process restarts through SQLite, and
completion transitions are also revisions. These local versions record observed
changes, not authoritative provider order. A stale Yahoo cache could appear as a
new local revision: without upstream sequence numbers this cannot be reliably
detected. The generic `store()` semantics above still apply to explicitly
versioned sources. Late candles are accepted and sorted by event time.

Five sessions are fetched on every poll to discover recent corrections and fill
short outages. Older-than-window gaps and changes are not recovered automatically.
Revisions are retained without pruning. One polling worker serves all clients;
run one Uvicorn process. Multi-process broadcasting needs a future shared broker.

## Status, freshness, and recovery

`GET /feed/status` and WebSocket status messages expose:

- Source, symbol, HTTP polling transport, WebSocket delivery, and the fixed label
  `latest_available_polled_not_guaranteed_realtime`, with `is_realtime=false`.
- State: starting, available, degraded (some bad rows), unavailable, or disabled.
- Last successful poll, latest event and first-receipt times, consecutive failures,
  generic error, skipped row count, and retry interval.
- Data age in seconds from newest candle start to now. Over 180 seconds means
  stale, with `data_kind=historical_or_stale`. No data means age null and stale true.
  The age updates even during a failure or market closure. It is not exchange latency.
- Market status open/closed based on Yahoo's last known regular session start/end;
  missing/invalid metadata gives unknown. This is not an independent exchange
  calendar and is labeled with its metadata basis. During outages the bounds may
  be cached. Extended-hours candles are intentionally excluded.
- Provider delay is unknown (`provider_delay_seconds=null`), regardless of freshness.

Failures retain cached observations, mark unavailable, and retry with delays
`min(300, poll_seconds * 2**consecutive_failures)` (60, 120, 240, 300 seconds at
the default interval). Success resets the delay to 30 seconds. Every retry uses a
new HTTP request; no upstream WebSocket connection is maintained. Closed markets
continue polling and expose old candles with their true age. No prices are
invented to fill closed sessions. A feed outage does not change `/health` if the
database remains readable. Cached candles are restored on restart, while last
successful poll/session metadata are intentionally unknown until a successful fetch.

## HTTP and WebSocket protocol

`GET /observations` returns status and the latest 390 current candles in ascending
event order. `WS /ws/market` accepts clients without starting another Yahoo worker.
The Vue dashboard consumes these same HTTP and WebSocket contracts.

Messages:

```json
{"type":"snapshot","status":{"is_realtime":false},"observations":[]}
{"type":"observations","observations":[]}
{"type":"status","status":{"is_realtime":false}}
```

These are abbreviated shapes: actual status objects include all fields described
above, and observations include every validated model field. Initial/reconnection
snapshots contain the latest 390 candles. Subsequent change batches contain at
most 100 candles each, including corrections to older candles in the poll window.
Status is sent after every poll and every five seconds when idle, so age remains
visible during retry backoff. Clients merge by identity and keep highest revision;
snapshot/update races can produce replays, which clients must handle idempotently.

Each client has a bounded 64-message queue and independent sender. On overflow,
the backend closes that client with code 1013 instead of silently losing updates.
Disconnected clients and sender tasks are removed. Clients implement reconnection
with backoff; a new connection gets a fresh snapshot. There is no durable event
cursor or automatic recovery for older history beyond that snapshot. SQLite is
the persisted record; WebSocket delivery is best effort and follows persistence.

## Configuration and validation

`YAHOO_FEED_ENABLED=false` disables upstream polling while leaving cached data,
health, and WebSocket snapshots available. `YAHOO_POLL_SECONDS` defaults to 30 and
must be at least 1. See README.md for startup and a terminal WebSocket client.

Tests use mocked chart JSON/HTTP responses and in-process clients; production
polling is disabled by default by a test fixture. Coverage includes HTTP timeout,
rate limiting and server failures, malformed and null data, OHLCV conversion,
time preservation, local revisions across restarts, completion changes, atomic
rollback, closed/unknown sessions, stale ages, backoff/reset, multi-client fan-out,
large bootstrap batches, slow consumers, reconnect snapshots, and lifespan cleanup.

Yahoo does offer quote streaming: the current yfinance project documents
[WebSocket and AsyncWebSocket clients](https://ranaroussi.github.io/yfinance/reference/yfinance.websocket.html).
This implementation selects polling to obtain OHLCV candles and a recovery window;
its downstream WebSocket does not make the upstream data real-time. Yahoo also
publishes [exchange coverage and delay information](https://help.yahoo.com/kb/SLN2310.html).

## Deferred work

Quote-tick streaming, HTTP ingestion, independent exchange calendars,
authentication, retention, pagination, durable client replay, and schema migrations
are deferred. No paid API is used.

Implementation references: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)
and [SQLite transactions](https://www.sqlite.org/lang_transaction.html).


## Vue dashboard architecture

Vue 3 single-file components are built with Vite. TradingView Lightweight Charts
5 renders the price and volume series. No frontend request goes directly to Yahoo;
FastAPI remains the only market-data source. `marketClient.js` handles transport,
`observations.js` validates and merges payloads, `MarketChart.vue` owns imperative
chart resources, and `App.vue` provides the responsive dashboard.

On mount, the client concurrently requests `/observations` and opens `/ws/market`.
Both snapshots merge by observation identity and keep the highest local revision;
unchanged replay revisions retain the original payload/receipt. A delayed HTTP
response cannot overwrite a newer WebSocket status or observation revision.
Candles are sorted by source event time. Invalid messages and foreign symbols or
sources are rejected without replacing valid displayed data.

The browser retains at most 2,000 received candles in memory; default chart view
shows the latest 390. All received exposes the retained browser-session window,
not a request for the complete SQLite history. Initial/reconnection backend
snapshots are limited to 390 saved candles. The ledger provides five exact records
with UTC event time, OHLC, volume, local revision, and inferred interval status.
No fake data, gap interpolation, or automatic historical-to-live relabeling exists.

The chart shows candlesticks and a synchronized histogram in separate panes.
Latest-candle revisions and appends use `series.update`; earlier corrections or
window changes rebuild sorted unique data while preserving the visible range.
Initial data and responsive resizes fit the received window; a fit control is
also available. Crosshair selection updates the OHLCV readout. The legend uses
observed close versus open for up/down coloring. The displayed percentage is the
change from the first displayed candle's open to the last displayed close; it is
explicitly not a daily return or live quote.

## Browser connection and visible states

Transport status is independent from the upstream feed status. A connected browser
may show Yahoo unavailable, a closed market, or stale candles. The header shows
connecting/connected/disconnected; the feed panel separately shows backend feed
state and last-known market state. The notice prioritizes disconnection, upstream
unavailability, closure, paused polling, staleness, and partial-data degradation.
The stale badge and age remain visible alongside closed/unavailable states.

The client refreshes age from actual displayed candle start timestamps every
second, so age does not freeze when heartbeats stop. At more than 180 seconds the
candles are visibly stale/historical. Receipt times preserve the first receipt of
each stored revision. Provider latency is unknown. The dashboard permanently says
polled/delay may vary and no real-time guarantee, regardless of backend status.
When no data exists, price fields are em dashes and loading/empty explanations
replace the chart foreground without fabricating candles.

Socket close/error triggers retry delays of 1, 2, 4, 8, 16, then 30 seconds, capped
at 30. A valid message resets retries. No valid message for over 20 seconds closes
the connection and initiates reconnect, covering silent failures. Each reconnect
also requests saved observations. HTTP failures are visible without deleting prior
data. Manual reconnect cancels pending retry, invalidates old socket callbacks,
and opens a new connection. Component teardown closes the socket, aborts HTTP,
cancels timers, and destroys chart/resize resources.

Saved observations require a reachable backend and an existing SQLite file. During
closed sessions or Yahoo outages those records remain available. During a backend
outage only the current browser's in-memory records remain; a fresh browser session
has no offline cache. Last-known market state is metadata, not a local holiday
calendar. Status and notices do not imply exchange-open verification.

## Responsive layout, attribution, and verification

Desktop places a large chart next to a feed monitor and provenance panel, with an
observation ledger below. At tablet/mobile sizes the layout stacks, supplementary
overview metrics collapse, and the table scrolls inside its own container. Price
and status remain readable at narrow widths. Buttons expose accessible labels,
pressed state, and focus styles; textual notices accompany colored indicators.
The ledger provides a non-canvas representation of recent candles.

TradingView attribution is visible in the footer and chart logo. The notice is
retained in `frontend/THIRD_PARTY_NOTICES.md`. Dependencies and exact resolved
versions are tracked by `frontend/package-lock.json`.

Vitest tests exercise timestamp preservation, revision and replay handling,
late-arrival sorting, malformed data rejection, REST/WebSocket races, reconnects,
silent failures, cleanup, exceptional dashboard states, OHLCV rendering, volume
updates, and older corrections. Test-only synthetic fixtures are never imported by
production code. Browser checks use actual Yahoo-backed saved observations;
closed-market history remains labeled historical. See README.md for Node setup,
both test suites, production build, and same-origin proxy configuration.


## End-to-end verification boundaries

Evidence is indexed in `docs/evidence/part4-1/README.md`. The normal production
entry point was tested first with a fresh temporary SQLite file and real Yahoo
HTTP access. Read-only checks compared actual proxied HTTP and WebSocket snapshots
against SQLite rows, including source event times, receipt times, OHLCV, and local
revision values. A later successful poll timestamp demonstrated the default
30-second background cycle (plus request time). SQLite commits occur before
broadcast, rather than after client rendering.

Subsequent process restarts used that same verification database with polling
disabled. Fresh-browser historical snapshots and the existing browser's automatic
reconnection were checked separately. A test-only entry point in
`backend/tests/evidence/server.py` injected HTTPX-mocked Yahoo HTTP 503 responses;
its `/__evidence/poll` route exists only in that test entry point. It was bound to
loopback for verification and is not part of the production FastAPI app. Mocked
outage/recovery evidence must not be presented as an actual Yahoo service outage.

Controlled append verification replayed five actual captured Yahoo candles,
starting with one and appending four. No prices, volume, or source event times
were invented. The replay batch's last-successful-poll time is the replay fetch
time, not a new provider receipt; original observation receipt times are retained.
The browser continues to label those candles stale/historical. Duplicate replay
was verified to create no additional rows or observation broadcasts. Separate
repository checks used explicitly mocked revision/completion metadata in an
isolated database without changing the captured prices. These tests validate
handling, not evidence that Yahoo issued a genuine correction during this run.

The exact assignment rubric has not been supplied. Thirty-second chart polling
plus downstream WebSocket push proves automatic delivery, but not a persistent
Yahoo streaming subscription, exchange tick coverage, or guaranteed real-time
prices. A strict upstream-streaming requirement remains unmet; rubric acceptance
cannot be certified. Open-market new-candle behavior and authentic upstream
corrections need an open-session observation if required for submission. No report
file or Git publication is included in this verification step.


## Official Prompt 05 addition — isolated streaming verification

`backend/app/services/yahoo_stream.py` uses the approved, pinned yfinance 0.2.66
AsyncWebSocket client to verify one AAPL quote within a caller-selected deadline.
It preserves the decoded source message, raw epoch-millisecond timestamp,
normalized UTC source time, handler-entry receipt time, and available volume fields.
Connection-open and subscription-sent flags are distinct from quote receipt.
No-message and error paths do not fabricate observations. Recent streamed quotes
are not labeled guaranteed real-time. The listener is cancelled before transport
closure to accommodate the pinned client's shutdown behavior.

This is a callable/CLI verifier, not an additional production background worker.
`backend.app.main:app`, the existing chart polling adapter, polling fallback,
existing candle schema, and Vue message contract remained unchanged in Prompt 05.
Prompt 06 below now adds raw-observation persistence; candle formation, volume-delta
computation, timeframe aggregation, and replay remain deferred. See `docs/verification.md` for the actual quote and test
results. Previous Step 5 polling evidence does not retroactively become streaming
evidence; the Prompt 05 capture is retained in its own evidence subdirectory.


## Official Prompt 06 — raw observation and arrival schema

Two additive tables are initialized by `Database.initialize()` alongside the
original `market_observations` table/view. No existing candle rows, indexes or
columns are replaced. Foreign keys are enabled on each application connection.
The authoritative DDL is `backend/app/db/raw_observations.py` (`RAW_SCHEMA`).

| Table / columns | Meaning |
| --- | --- |
| `raw_observations.observation_id` | AUTOINCREMENT durable local version id |
| `source`, `symbol` | Adapter source and exact source ticker |
| `payload_json` | Canonical JSON retaining every decoded source field and JSON type |
| `source_time_ms`, `source_time_utc`, `source_timestamp_units` | Normalized source time and explicit epoch-millisecond unit |
| `first_receipt_time_utc` | UTC receipt of the first recorded occurrence of this version |
| `revision` | Local correction-chain ordinal, starting at zero; not a Yahoo sequence |
| `supersedes_id`, `correction_evidence` | Explicit prior-version FK and evidence text; null for independent messages |
| `observation_arrivals.arrival_sequence` | AUTOINCREMENT, database-wide accepted-arrival order |
| `observation_id` | FK to the exact stored source version for this receipt |
| `receipt_time_utc` | This receipt's timestamp, including for repeat messages |
| `disposition` | inserted, duplicate, or revision |
| `correction_evidence` | Caller evidence accompanying this receipt, if any |

Canonical identity is `(source, symbol, payload_json, correction target or none)`.
Object-key order and JSON whitespace are ignored; source values/types and array
order are retained. For example, a string source timestamp remains a string in
JSON even though the indexed `source_time_ms` is an integer. This is exact equality
of decoded fields in the same correction context, not byte-for-byte protobuf
identity and not a heuristic based only on price/timestamp/volume. Identical fields
may be indistinguishable from a repeated economic event: the source supplies no
reliable event id here. All receipts remain traceable even when deduplicated.

`record()` uses `BEGIN IMMEDIATE` to find/insert the observation version and append
its arrival in one transaction. Unique indexes and serialized writes prevent
concurrent duplicates from creating multiple versions. Every accepted repeat
appends a duplicate journal row but does not change the first receipt or payload.
Arrival sequence continues after restart and remains authoritative when source
time arrives late or receipt clocks coincide/move backward. It is serialized
recording order, not an inferred global order between independent receiver
processes; run a single sequential Yahoo receiver for original callback order.

A same-timestamp payload change creates another independent revision-zero root.
It is **not** automatically a correction. A knowledgeable adapter can call
`record(..., correction_of=id, correction_evidence=...)` only when an explicit
source-level relationship is available. This API requires a nonblank evidence
string but cannot independently prove the caller's assertion. Targets must exist
for the same source/symbol, and a target cannot fork into conflicting successors.
The new version links to its predecessor and gets predecessor revision plus one.
A correction may restore an older price without losing that version's identity.
A repeat correction with the same target/payload adds a duplicate receipt only.

The current Yahoo quote adapter supplies **no correction target**, because the
observed feed has no proven correction identifier. Explicit-revision verification
therefore uses clearly labeled constructed data. No claim of a genuine Yahoo
correction is made. A source-provided field that merely happens to be named
revision/correction is retained as data, not blindly trusted as identification.

### Reading without duplicate counting or future-state leakage

Inspect every receipt, including repeats and version metadata, in durable order:

```sql
SELECT a.arrival_sequence, a.receipt_time_utc, a.disposition,
       o.observation_id, o.symbol, o.source_time_ms, o.payload_json,
       o.revision, o.supersedes_id
FROM observation_arrivals AS a
JOIN raw_observations AS o USING (observation_id)
ORDER BY a.arrival_sequence;
```

For subsequent processing, skip `disposition='duplicate'`. Treat `inserted` as
an independent observation and `revision` as a replacement of `supersedes_id`.
Do not add revisions as extra trades or sum cumulative `day_volume` values.
`RawObservationRepository.arrivals(after_sequence=...)` offers read-only journal
inspection; it neither deletes data nor computes/replays candles. Future replay
must apply revisions when their arrival is encountered, not substitute the latest
version into earlier history. Prompt 07/09 processing is explicitly deferred.

### Receiver integration and scope

The CLI in `yahoo_stream.py` initializes a chosen database and injects the recorder
into the existing yfinance callback. It records valid quotes synchronously before
capture acknowledgement, preserving one receiver's callback order and reporting
persistence failures separately from successful network receipt. Direct Python
callers may inject a recorder or omit it for diagnostic-only use; the CLI records
by default. `--max-quotes` sets a bounded receipt target; there is still no new
continuous upstream background worker in FastAPI. Existing polling and the
original candle-history paths remain independent and operational.

Decoded valid observations retain all raw fields; malformed/control/nonmatching
messages are counted and ignored by the quote validator. No protobuf-byte capture,
exchange calendar, volume normalization, candle aggregation, new frontend contract,
or playback engine has been added in Prompt 06. Verification details, expected
versus observed rows, and actual database paths are in `docs/verification.md`.


## Official Prompt 07 — one-minute calculation from the raw journal

`backend/app/services/minute_candles.py` is a separate read-only derivation layer
on `RawObservationRepository.arrivals()`. No schema changes or new persisted candle
versions are introduced. The original polled `market_observations` model, existing
HTTP/WebSocket routes, and Vue chart remain independent and unchanged.

The pure function `form_one_minute_candles()` and saved-repository wrapper
`MinuteCandleCalculator` return an envelope with symbol, source, calculation time,
`candles`, and `metadata`. Each candle contains **exactly** `timestamp`, `open`,
`high`, `low`, `close`, `volume`. Timestamp is the minute start in UTC (`...:00Z`).
Unknown volume is JSON null; it never requires relaxing the separate polled-candle
model's integer-volume constraint. Metadata keyed by the same timestamp contains
`is_complete`, forming/complete state, completion basis, known/unknown volume
status/reasons, and effective observation count. Coverage metrics for larger
intervals are not implemented in this prompt.

### Availability, corrections, and price order

1. Read arrivals in durable `arrival_sequence` order. The as-of snapshot stops at
   the first receipt after the requested time. Using a prefix rather than skipping
   a future row also avoids admitting a later correction early if receipt clocks
   move backward. This is a calculation snapshot, not a timed replay player.
2. Ignore duplicate receipts. Apply explicitly identified corrections by replacing
   their predecessor while retaining the original observation's ordering position
   for source-time ties. Do not infer corrections for distinct same-time messages.
3. Exclude observations whose source time is later than the calculation time.
   Invalid saved prices produce a validation error rather than synthetic prices.
4. Sort effective observations by UTC source timestamp, with original arrival
   order as a deterministic tie-break where the feed cannot order equal timestamps.
5. Bucket by UTC minute; first/last prices become open/close, extrema high/low.
   No observations means no candle: empty minutes are not forward-filled.

A late observation can revise open, high, low, or close even after interval end.
A correction can change the source minute itself. Each calculation recomputes the
available effective history, so removal/replacement affects both old/new buckets
and any downstream cumulative-volume baseline. Future receipts never alter an
older as-of calculation. Data is not overwritten in either raw or polled tables.

### Cumulative-volume policy

For AAPL's reported `day_volume`, the prior effective source-time observation is
a usable baseline only if it has a valid nonnegative integer counter, matches the
source/exchange and New York local date, and lies in the same or immediately
preceding minute. New York date uses `zoneinfo`; it is a daily-counter partition,
not a full market-calendar implementation or an assertion about every provider's
session definition. Only AAPL is supported until another symbol's policy is defined.

For each point with a valid baseline, add `current_counter - previous_counter`.
Summing those differences is equivalent to the ending cumulative total minus the
baseline when the sequence is fully valid. Never sum cumulative totals themselves.
The official example gives `(1004-1000)+(1009-1004)+(1011-1009)=11`.

If any contribution is indeterminate, the **whole affected candle volume is null**,
with explanatory metadata. This conservatively avoids presenting a known subtotal
as the full minute volume. Reasons include missing/invalid counters or baseline,
a counter decrease/reset, an unobserved-minute gap, or distinct conflicting
counters at one source timestamp. Such a boundary also cannot be used as an
unambiguous baseline for the next contribution. A subsequent reliable observation
can establish a fresh baseline for a later minute. The reset-affected minute stays
unknown; the reset value is not treated as volume traded from zero. Across a
New York date or exchange change the prior total is never subtracted.

Equal counters with a valid baseline give **known zero**, distinct from unknown.
Late middle counter values do not add volume twice: recalculation reorders points
and partitions the same total difference. Corrections to a minute's last counter
also change the next minute's volume. Missing-minute counter changes are not
arbitrarily assigned to the next visible candle.

This is volume derived from available quote observations, not a promise of exact
exchange-bar totals or complete trade coverage. No counter extrapolation to an
unobserved minute boundary is performed. Unknown intermediate counter evidence is
handled conservatively even when a partial subtotal is known.

### Completion and scope

A minute is forming when calculation time is before its end, and complete when
at or after its end. Completion is clock-based and independent of whether volume
is known; it does not assert finality, feed freshness or full observation coverage.
The caller supplies an aware as-of clock for repeatable checks. The service and CLI
do not fetch new Yahoo data, mutate the saved journal, or write chart history.

Tests and exact expected/observed results are in `docs/verification.md` and
`docs/evidence/part4-1/prompt07/`. That section records the Prompt 07 stage.
Prompt 07A below adds aggregation/calendar behavior; Prompt 08 raw-candle chart
delivery and Prompt 09 playback remain deferred.


## Official Prompt 07A — higher-timeframe history architecture

`HistoryService` in `backend/app/services/history.py` creates one available
one-minute snapshot and passes it to `aggregate_minutes()` for every interval.
The new `/history` endpoint defaults to `1m`, `symbol=AAPL`, `dataset=raw`, with
an optional aware `as_of`. It runs calculation off the ASGI event loop. Invalid
parameters/clocks are 422; SQLite failures are 503. No Prompt 08 subscription,
frontend selector or Prompt 09 player is added. Vite proxies this new HTTP path;
existing chart/polling endpoints retain their old contract and behavior.

### Calendar and bucketing

`exchange_calendar.py` wraps `pandas_market_calendars` and caches regular sessions
by New York local trading date. Version 4.6.1 is pinned because a newer release
with apparently compatible metadata failed to import on Python 3.9. Its NASDAQ
alias is internally the shared NYSE US-equity calendar. All ten full-day Nasdaq
2026 closures and both early closes were verified against the
[official Nasdaq schedule](https://nasdaqtrader.com/Trader.aspx?id=Calendar).
Tests also cover both 2026 DST transitions. Calendar lookup is offline and free;
no runtime provider account/network request is needed. Pinned package rules do
not substitute for future exchange announcements, unscheduled halts or calendar
maintenance. AAPL is the only configured exchange/symbol mapping.

Regular sessions are half-open `[market_open, market_close)`. Premarket, postmarket,
weekend and holiday minute bars are excluded. For intraday size N, group start is
`session_open + floor((minute - session_open) / N) * N`; group end is the earlier
of start+N and session close. Thus hourly bars start at :30 rather than UTC :00,
and the last normal/early-close hour can be 30 minutes long. Daily group start/end
are the actual session open/close; the timestamp is its UTC open. Group keys include
session date, so gaps do not split a day or merge it with the next session.

### Aggregation and coverage

Input candles must be resolved unique one-minute timestamps, aligned to minute
boundaries. Duplicate base timestamps raise an error instead of silently summing
revision versions. The upstream raw calculator or polling repository resolves
versions first. After source-time sorting, aggregation takes first open, maximum
high, minimum low, last close, and sum of available minute volumes. Any unknown
minute volume propagates null; numeric zero remains known. Missing minutes are not
synthesized. A partial three-minute sample therefore sums to 3550 with partial
coverage, rather than inventing two zero-volume minutes or claiming full volume.

The six candle fields are unchanged. Per-candle metadata carries:

- interval end and session date/open/close;
- full scheduled `expected_minutes`, `available_minutes`, `complete_minutes`, and
  missing-minute count;
- coverage full only when every scheduled minute is available and complete;
- interval completion only after the interval end and completion of all present
  minute states (this is not provider finality or a complete-trade guarantee);
- volume known/unknown reasons and `volume_basis=sum_of_available_minute_volumes`.

Coverage and completion are independent: a finished five-minute interval with
three complete minutes is complete but partial (3/5). A regular daily denominator
is 390, early close is 210, and a shortened final hour uses 30, not 60. Coverage
measures minute-grid availability, not whether every exchange trade was captured.

### As-of availability, corrections, and datasets

The raw dataset always invokes the existing Prompt 07 calculator at the displayed
clock, so only the available arrival prefix and effective correction chain enter
base candles. Late observations, corrected source times and cumulative-volume
boundary changes are recomputed on each request; larger intervals are derived
again from that snapshot. No aggregate cache or persisted aggregate can become
stale. The aggregation layer rejects a base snapshot calculated at a future clock,
filters future source minutes, and respects any explicit minute availability time.
A final historical minute is never backfilled into a prior displayed state.

The optional `polling` dataset calls the new read-only
`ObservationRepository.current_as_of()`: filter by source/receipt cutoff first,
then choose each observation identity's highest available revision. This avoids
the latest-version view leaking a later correction. The original `current()` and
`/observations` remain unchanged. Raw and polling datasets are never mixed or
silently substituted; source/dataset fields preserve provenance. An empty dataset
returns empty arrays. Existing stored candles/raw arrivals are never rewritten.

This is request-time history calculation, not timed arrival playback, and no
trading-session selector/replay transport or chart timeframe switching is included.
The frontend's sole change is same-origin `/history` proxy configuration.

## Prompt 08 current dashboard integration

The persistent Yahoo subscription now drives the Vue chart through committed raw
observations and the Prompt 07/07A calculator. See [design-pipeline.md](design-pipeline.md)
for current lifecycle, protocol, fallback, timestamp and coverage semantics. Earlier
polling-only and planned-integration descriptions record prior milestones. Prompt 09
arrival-order timed replay remains pending.

## Prompt 09 replay integration

Read-only arrival-order replay is now available in Vue through `/replay/catalog`
and `/replay/frame`, sharing live candle calculations and preserving receipt-prefix
isolation. See [design-pipeline.md](design-pipeline.md) for range, baseline, clock,
revision and frozen-boundary semantics. Earlier pending-Prompt-09 notes describe
prior milestones. No schema migration was needed.
