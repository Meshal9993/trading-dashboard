# Trading Dashboard

**IST 402 — Assignment 4, Part 4.1, Prompt 09**

Repository: https://github.com/Meshal9993/trading-dashboard

The FastAPI backend maintains an AAPL Yahoo Finance WebSocket subscription,
commits raw observations and arrival history to SQLite, calculates candles, and
streams derived states to the Vue dashboard. Select 1m, 5m, 15m, 1h or daily
candles. The free 30-second Yahoo HTTP poller remains a separately labeled fallback.
Unknown volume stays unknown; neither source guarantees real-time exchange prices.
No paid APIs or credentials are used. Prompts 08 and 09 are implemented, including read-only arrival-order replay.

See [the current pipeline](docs/design-pipeline.md) and
[verification results](docs/verification.md). The prompt sections below document the individual implementation steps.

## Setup and startup

Requires Python 3.9+ with SQLite support. From the repository root:

```sh
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
python -m uvicorn backend.app.main:app --reload
```

For runtime-only installation use `backend/requirements.txt`. Use one server
worker: the poller and WebSocket subscriptions are local to the process.
Startup initializes SQLite and starts one shared subscription and one polling
worker, regardless of client count. The first Yahoo request is asynchronous; the service can be healthy while
the market feed is unavailable.

| Setting | Default | Purpose |
| --- | --- | --- |
| `DATABASE_PATH` | `backend/data/market.sqlite3` | Database file; default is resolved against backend directory |
| `YAHOO_FEED_ENABLED` | `true` | Set `false` to disable HTTP polling |
| `YAHOO_STREAM_ENABLED` | `true` | Set `false` to disable the upstream Yahoo WebSocket; disable both feed flags for offline operation |
| `YAHOO_POLL_SECONDS` | `30` | Seconds between successful polls; minimum 1 |

Relative custom database paths use the process working directory. Startup creates
parent directories and missing schema objects, preserving existing data. Existing
schemas are not migrated automatically; Prompt 06 additively creates its two
new raw-recording tables without replacing the existing candle history.

## Frontend dashboard

Requires Node.js 22.12+ (Node 22 LTS recommended) and npm. Keep the backend running
on port 8000, then open another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open the Vite Local URL (normally http://127.0.0.1:5173). The development server
proxies HTTP requests and WebSockets to FastAPI. If the backend uses another
address, set `VITE_BACKEND_TARGET` when starting Vite:

```sh
VITE_BACKEND_TARGET=http://127.0.0.1:8001 npm run dev
```

The dashboard includes:

- AAPL OHLC candles in 1m, 5m, 15m, 1h, and 1d views with synchronized volume when known, powered
  by TradingView Lightweight Charts. Chart time, event time, and receipt time are UTC.
- WebSocket updates, revision-aware merging, automatic reconnect with capped
  exponential backoff, a manual reconnect button, and HTTP saved-data fallback.
- Latest observed close (not a live quote), displayed-window change, OHLC readout,
  source, candle age, last successful poll, feed state, and market-session state.
- Loading, empty, disconnected, degraded, unavailable, stale, closed-market, and
  paused-polling states. Previously loaded candles stay visible through interruptions.
- A latest-390/all-received window selector, chart fit control, zoom/pan, and the
  latest five observations in an accessible ledger. All received means candles
  received in this browser session, capped at 2,000; it is not the full database.
- Responsive desktop/mobile layouts and a horizontally scrollable ledger on small
  screens. No simulated prices or demo data are included in the application.

To review saved candles or use Replay without contacting Yahoo, start the backend
with both `YAHOO_FEED_ENABLED=false` and `YAHOO_STREAM_ENABLED=false` and point
`DATABASE_PATH` at a database containing the recorded observations. An open
dashboard retains received candles during a backend interruption. A fresh page
needs the local backend to read SQLite.

### Frontend validation and production build

From `frontend/`:

```sh
npm test
npm run build
npm run preview
```

`npm test` runs Vitest with Vue Test Utils and mocked network/chart dependencies.
Synthetic fixtures exist only in `frontend/tests/`. `npm run build` writes the
production app to ignored `frontend/dist/`. `npm run preview` previews that build
and proxies the backend, normally at http://127.0.0.1:4173. It is a local check,
not a production hosting server.

For production hosting, serve `frontend/dist/` at the site root and reverse-proxy
`/replay/`, `/observations`, `/history`, `/feed/`, `/health`, and `/ws/` to FastAPI on the same origin,
including WebSocket upgrade headers and timeouts that permit the five-second
heartbeat. HTTPS pages automatically use `wss:`. No permissive CORS policy is
needed. Hosting/deployment is outside the current scope.

## Endpoints and inspection

| Endpoint | Behavior |
| --- | --- |
| `GET /health` | Database read health; HTTP 200 or 503 |
| `GET /feed/status` | Feed state, source, market session status, timestamps, data age, retry state |
| `GET /observations` | Latest 390 current AAPL candles and feed status |
| `GET /history` | Calendar-aware history for all five timeframes, with raw, polling, and automatic dataset selection |
| `WS /ws/candles` | Dashboard candle snapshots and updates with provenance, coverage, and feed status |
| `GET /replay/catalog`, `GET /replay/frame` | Recorded ranges and read-only arrival-order playback |
| `WS /ws/market` | Initial snapshot, changed observation batches, status updates |
| `GET /docs` | Interactive HTTP API documentation |

```sh
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/feed/status
curl http://127.0.0.1:8000/observations
```

To inspect the legacy polling WebSocket without a frontend, run this from another
terminal with the virtual environment activated:

```sh
python - <<'PY'
import asyncio
from websockets.asyncio.client import connect

async def main():
    async with connect('ws://127.0.0.1:8000/ws/market', max_size=4 * 1024 * 1024) as socket:
        async for message in socket:
            print(message)

asyncio.run(main())
PY
```

Clients receive `snapshot`, `observations`, and `status` JSON messages. Each
observation includes OHLCV, symbol, source, source event time, first receipt time,
local revision, and inferred candle completion. Clients should upsert by
`(symbol, source, observation_id)` using the highest revision. Reconnect after a
connection loss to receive a fresh snapshot. Slow clients are closed with code
1013 and must reconnect; there is no durable WebSocket replay cursor yet.

## Freshness and Yahoo transport

FastAPI subscribes to Yahoo Finance's AAPL quote WebSocket through
`yfinance==0.2.66`. The backend saves the raw quotes and their arrival order in
SQLite, derives regular-session candles, and sends chart snapshots and updates to
Vue through `/ws/candles`.

An independent HTTP worker polls Yahoo's one-minute chart endpoint every 30 seconds
by default. When the stream is unavailable or stale, the dashboard can display
these separately labeled polling candles or retained history. The two datasets
remain separate.

Stream freshness uses the newest source observation timestamp. Polling freshness
uses the latest provider timestamp. Data older than 180 seconds is stale. Data age
measures time since the reported source observation. Yahoo latency and trade
coverage are unverified. The dashboard shows source and backend receipt times
separately and keeps saved regular-session candles available when the market closes.

## Persistence

`market_observations` stores symbol, source, observation ID, event and receipt
timestamps, OHLC prices, volume, revision, and completion. Its primary key is
`(symbol, source, observation_id, revision)`. The current-observation view selects
the highest revision per identity. Yahoo does not supply chart revision numbers:
changed snapshots get atomic, locally increasing revisions, including when the
candle's inferred completion changes. Exact replays preserve the first receipt.
These versions capture arrival history and cannot prove provider update order.

See [docs/design.md](docs/design.md) for schema and protocol details.

## Tests

```sh
backend/.venv/bin/python -m pytest backend/tests -q
```

Tests use temporary databases, synthetic chart fixtures, HTTPX mocked Yahoo
responses, and in-process WebSockets. External polling is disabled by default in
tests; no test requires network access, API credentials, or payments.

## Structure and Git workflow

Backend entry point: `backend/app/main.py`; adapters and polling: `backend/app/services/`;
SQLite repository: `backend/app/db/`; models: `backend/app/schemas/`; tests:
`backend/tests/`. Frontend entry: `frontend/src/App.vue`; chart:
`frontend/src/components/MarketChart.vue`; protocol and data rules:
`frontend/src/services/`; frontend tests: `frontend/tests/`.

The local `main` branch has `origin` configured to this repository. No commits or
pushes are authorized. Review changes and remote history before publishing.


## End-to-end verification evidence

Actual run outputs, JUnit results, HTTP/WebSocket/SQLite comparisons, screenshots,
and their provenance are indexed in [docs/evidence/part4-1/README.md](docs/evidence/part4-1/README.md).
Verification used separate temporary SQLite files; normal saved application data
was not overwritten. The test-only harness under `backend/tests/evidence/` can
inject a mocked upstream HTTP 503 and replay an actual Yahoo capture. Its control
route must never be deployed with the production entry point.

The final checks traced a genuine Yahoo WebSocket quote through SQLite, candle
calculation, FastAPI WebSocket delivery, and the visible chart. A simulated upstream
interruption verified the polling fallback and recovery. Two offline runs of the
same 69 recorded arrivals produced identical replay results and unchanged SQLite
records.

- [Live streaming and recovery evidence](docs/evidence/part4-1/prompt08/)
- [Final test and replay evidence](docs/evidence/part4-1/prompt09/), including 182
  backend tests, 40 frontend tests, and a successful production build
- [Demonstration video (53.7 seconds)](docs/evidence/part4-1/final-demo.mov)

## Official Prompt 05 — bounded Yahoo WebSocket check

The approved dependency `yfinance==0.2.66` is included in backend requirements for
the existing Python 3.9 environment. Run this independent backend check:

```sh
backend/.venv/bin/python -m backend.app.services.yahoo_stream \
  --symbol AAPL --timeout 60 --output /tmp/aapl-stream-check.json
```

It captures one genuine quote and records valid arrivals in SQLite by default,
or reports receipt unverified after the deadline. See Prompt 06 below for database
selection and multi-quote capture.
The result distinguishes connection opening, subscription sending, quote receipt,
and cleanup; a missing quote/failure exits with code 1. Source time is Unix epoch
milliseconds, normalized to UTC alongside the distinct backend receipt timestamp.
Reported `day_volume` is cumulative day/session volume, not per-trade volume.

A genuine AAPL quote was verified on November 10, 2026. See
[docs/verification.md](docs/verification.md) for exact data, commands, tests, the
initial cleanup issue and corrected run. This command is a bounded diagnostic.
The dashboard uses the persistent subscription described in Prompt 08.


## Official Prompt 06 — raw WebSocket observation recording

The bounded receiver now persists each accepted quote before reporting capture
success. It records the complete **decoded Yahoo message** (including unknown
source fields), ticker, original source timestamp in the JSON payload, normalized
epoch-millisecond/UTC source time, and backend handler-entry receipt time. It does
not retain the original protobuf bytes or JSON formatting from the network.

Default database: `backend/data/market.sqlite3` (same file as the existing candle
history). Override using `DATABASE_PATH` or the CLI's `--database` option, which
wins over the environment variable. Both FastAPI startup and the capture command
create missing schema objects automatically. To initialize explicitly from the
repository root:

```sh
backend/.venv/bin/python -c "from pathlib import Path; from backend.app.db.database import Database; Database(Path('backend/data/market.sqlite3')).initialize()"
```

Capture up to three valid incoming messages in a disposable file:

```sh
backend/.venv/bin/python -m backend.app.services.yahoo_stream \
  --symbol AAPL --timeout 60 --max-quotes 3 \
  --database /tmp/prompt06-capture.sqlite3 --output /tmp/prompt06-capture.json
```

The capture target counts valid receipts, including duplicates, not distinct
observations. The default target remains one. A deadline before the requested
count produces a nonzero exit even if some quotes were received and recorded;
receipt and recording results are reported separately. Persistence errors are
reported and do not produce a successful capture exit. The serial callback
commits each message before accepting the next, avoiding writes left running
behind a cancelled capture; a SQLite lock may wait up to the 10-second connection
busy timeout in addition to the network deadline and cleanup budget.

- `raw_observations` stores distinct source messages and explicit correction
  versions. Exact decoded-field repeats reuse the observation id.
- `observation_arrivals` stores **every accepted receipt**, including duplicates,
  with an AUTOINCREMENT `arrival_sequence` and its own receipt time. Sort this
  sequence to inspect the original recorded order, not the source timestamps.
- A changed payload at the same source time is a separate observation unless a
  caller supplies an explicit correction target and evidence. The current Yahoo
  adapter does not infer corrections. Earlier versions are retained.
- Readers process nonduplicate arrivals once and apply explicit correction links
  as replacements. The arrival journal also supplies the order for Prompt 09 replay.

See [schema and reader rules](docs/design.md#official-prompt-06--raw-observation-and-arrival-schema)
and [expected/observed verification](docs/verification.md#official-prompt-06--recording-verification).
Prompt 07 calculates candles and volume deltas from this journal. The receiver
counts and ignores invalid or nonquote messages.


## Official Prompt 07 — candles from saved observations

The raw-observation path now has a separate, read-only one-minute calculator:

```sh
backend/.venv/bin/python -m backend.app.services.minute_candles \
  --database /tmp/prompt06-capture.sqlite3 --symbol AAPL \
  --output /tmp/prompt07-candles.json
```

Use an existing database populated by the Prompt 06 receiver. `--database` defaults
to `DATABASE_PATH` or `backend/data/market.sqlite3`; this calculator does not create
or initialize a database. For a specified calculation time add, for example,
`--as-of 2026-11-09T15:01:00Z`. Times must include a timezone and are normalized to UTC.
An empty journal returns empty candle/metadata arrays, not placeholder prices.

Python callers can use `MinuteCandleCalculator(raw_repository).calculate(as_of=...)`
in `backend/app/services/minute_candles.py`. It returns `candles` containing exactly
`timestamp, open, high, low, close, volume` and a separate `metadata` array keyed
by timestamp for completion, volume-known/unknown reasons and observation count.
Candle timestamps are UTC minute starts. Prices use source-time order; duplicate
receipts are skipped and only explicitly linked correction versions replace
older observations. Late arrivals and corrections are reflected on recalculation,
including effects on a following minute's cumulative-volume baseline.

Volume is the sum of nonnegative consecutive cumulative `day_volume` differences,
with a known same-source/exchange/New-York-date baseline in the same or preceding
minute. Missing baselines/counters, resets/regressions, conflicting same-time
counters and missing-minute gaps produce `volume: null`. A verified unchanged
counter with a valid baseline produces numeric zero. No total is invented for
unobserved intervals. The New York date is a conservative daily-counter partition,
not the exchange-calendar/session engine required by Prompt 07A.

`is_complete` means the UTC minute has elapsed at calculation time; it does not
mean the provider can no longer revise it or that every exchange trade was seen.
As-of calculations use only the available arrival-journal prefix and source times
at or before that time, preventing future receipts/corrections from leaking in.

The official constructed example produces `O=100, H=103, L=100, C=101, V=11`,
with forming status before 15:01 UTC and complete status at/after 15:01 UTC.
See [Prompt 07 verification](docs/verification.md#official-prompt-07--one-minute-candle-verification)
for exact inputs, late-arrival results, unknown-volume cases, tests and evidence.

The live dashboard and Replay both use this calculator through the calendar-aware
history service described below.

## Official Prompt 07A — calendar-aware history

Install the updated backend requirements in the existing virtual environment.
`pandas_market_calendars==4.6.1` is pinned for Python 3.9 compatibility. Its offline
NASDAQ calendar alias uses the shared NYSE regular-equity schedule; 2026 closures
and early closes are checked against Nasdaq's official calendar. No paid calendar
API or runtime network lookup is used. Library rules need review when exchanges
announce exceptional closures or change their schedules.

A new, read-only endpoint serves all supported intervals from the same one-minute
snapshot. The existing `/observations` and `/ws/market` contracts are unchanged:

```sh
curl 'http://127.0.0.1:8000/history?symbol=AAPL&timeframe=5m'
curl 'http://127.0.0.1:8000/history?symbol=AAPL&timeframe=1d&as_of=2026-11-09T21:00:00Z'
curl 'http://127.0.0.1:8000/history?symbol=AAPL&timeframe=1h&dataset=polling'
```

| Parameter | Default | Values / purpose |
| --- | --- | --- |
| `symbol` | AAPL | Currently only AAPL is configured |
| `timeframe` | `1m` | `1m`, `5m`, `15m`, `1h`, `1d` |
| `dataset` | `raw` | `raw`: Prompt 07 calculation from saved WebSocket observations; `polling`: saved Yahoo one-minute chart candles |
| `as_of` | Current UTC | Timezone-aware displayed time; only observations/versions received and source-dated by that time are eligible |

Datasets are explicitly selected, never silently combined or swapped. An empty raw
journal returns no candles; use `dataset=polling` to inspect the separate polling
fallback history. Invalid parameters/naive clocks return 422, unavailable SQLite
returns 503. Vite's development/preview proxy forwards `/history` on the same origin.

Intraday buckets begin at the regular-session open (09:30 New York for AAPL),
not the top of each UTC hour. Daily buckets cover one trading session and use its
open as their UTC timestamp. DST, holidays/weekends and early closes come from the
calendar. Session close is exclusive; the final hourly bucket may be only 30
minutes. No missing candles are invented, and gaps never split a daily session.

Each candle still has exactly `timestamp, open, high, low, close, volume`.
Separate metadata includes interval/session bounds, expected/available/complete
minute counts, missing minutes, coverage status, completion and volume status.
Completion and coverage are independent: an ended five-minute interval can have
only three recorded minutes. Known volume is the **sum of available minute volumes**;
partial coverage does not turn that sum into a full-interval trading-volume claim.
Any unknown contributing minute volume yields null; known zero stays zero.

Raw history recalculates corrections/late arrivals at the requested clock before
aggregation. Polling history selects the highest revision actually received by
that clock rather than using today's latest-version view. Recalculation is
read-only; this as-of API is not a Prompt 09 replay player.

See [Prompt 07A verification](docs/verification.md#official-prompt-07a--higher-timeframe-verification)
for the official 3,550-volume example, calendar checks, API tests and evidence.

## Prompt 08 live dashboard API

- `GET /history?symbol=AAPL&timeframe=5m&dataset=auto` selects available raw
  observations or separately labeled polling fallback, with feed status.
- `WS /ws/candles?symbol=AAPL&timeframe=5m` sends authoritative snapshot/candles
  envelopes with completion and coverage metadata, plus five-second heartbeats.
- Explicit `dataset=raw` (default) and `dataset=polling` history remain available.
  Only explicit datasets accept `as_of`; automatic selection uses the current clock.
- Legacy `/observations`, `/feed/status` and `/ws/market` preserve polling contracts.

Timeframe changes request matching history and reconnect the browser subscription.
The backend upstream remains shared and running. Browser disconnection and upstream
unavailability are displayed independently. A failed/stale upstream selects polling
fallback; fresh recovered quotes select raw again without blending the datasets.
Revised old bars are replaced, forming bars update, and new intervals append.

Validation evidence is under `docs/evidence/part4-1/prompt08/`: genuine Yahoo quote
and visible-chart/SQLite comparisons, controlled upstream interruption/recovery,
automated tests and production build. Real quote volume was not safely attributable
to minutes and is shown as unknown. No trade-complete coverage or latency guarantee
is claimed.

## Prompt 09: Replay a saved session

1. Run the backend against the SQLite database containing recorded **raw Yahoo
   WebSocket observations**. For an offline session, disable both external workers:

   ```sh
   DATABASE_PATH=/absolute/path/to/recording.sqlite3 YAHOO_STREAM_ENABLED=false YAHOO_FEED_ENABLED=false \
     backend/.venv/bin/python -m uvicorn backend.app.main:app
   ```

2. Start Vue with `npm run dev` from `frontend/`, open the dashboard and select
   **Replay**. Select the saved symbol and inclusive UTC receipt range (ISO strings
   must include `Z` or an offset). Only AAPL is supported today.
3. Click **Load / restart replay**, then **Play replay** or **Next observation**.
   Pause as needed. The chart is explicitly labeled Replay and displays its clock,
   source timestamp, original receipt, durable arrival number and progress.
4. Change timeframe to recalculate the same arrival position. Restart to repeat the
   selection. Select **Live dashboard** to return to current feed behavior.

Replay is read-only. It processes original receipt order, including late source
observations, duplicate receipt events and identifiable revisions. It uses the same
candle calculator as live mode, with only the journal prefix revealed so far.
Pre-range context supplies volume baselines, correction chains and prior prices
within touched intervals; no later revision can leak into an earlier step. Unknown
volume stays unknown. A completed playback can still end with a forming candle.

Playback uses fixed 300 ms steps plus request time rather than original gaps.
The replay clock advances with recorded receipts (monotonic if the recorded system
clock moved backward). It does not use the current wall clock. This is recorded
history, never a real-time feed. Empty ranges display a clear message. Saved polling
bars alone do not constitute a raw observation replay recording.

Read-only APIs: `GET /replay/catalog`, `GET /replay/frame` with `symbol`, `start`,
`end`, `timeframe`, `cursor`, `high_water`, `advance`. The initial response freezes
the run's high-water arrival boundary. A new run can select a fresh boundary.
For deployment, proxy `/replay/` to FastAPI alongside the other API paths.

See [verification](docs/verification.md) and `docs/evidence/part4-1/prompt09/` for
real offline replay, two identical 69-step runs, SQL OHLC/unknown-volume checks,
unchanged full-record hashes/counts, screenshots and automated tests.
