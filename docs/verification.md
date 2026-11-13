# Official prompt verification

Earlier sections preserve each stage's results and limitations at that time.
Prompt 06 added raw recording, Prompt 07 added one-minute calculation, and the
final Prompt 07A section documents calendar-aware higher-timeframe history. Later additions do not
retroactively change earlier evidence.

## Prompt 05 — Yahoo Finance upstream WebSocket

Scope: bounded AAPL subscription only. No Prompt 06–09 implementation, database
recording, candle aggregation, or frontend integration is included. The existing
FastAPI dashboard continues to use its HTTP polling fallback unchanged.

## Environment, approval, and installation

The owner explicitly approved `yfinance==0.2.66` in the existing `backend/.venv`.
The interpreter is Python 3.9.6 on macOS ARM64. Before installation, yfinance was
absent; websockets 15.0.1 was already installed. The chosen release supports
Python 3.9 and supplies `AsyncWebSocket`. No new interpreter or environment was
created. No paid API, Yahoo account, or credentials were used.

Actual installation command, from the repository root:

```sh
backend/.venv/bin/python -m pip install -r backend/requirements.txt 'yfinance==0.2.66'
```

Installation succeeded. Import verification confirmed yfinance 0.2.66,
`yfinance.AsyncWebSocket`, and websockets 15.0.1 in that interpreter.
`backend/.venv/bin/python -m pip check` reported no broken requirements.
Resolved dependencies are retained in
`docs/evidence/part4-1/prompt05/installed-dependencies.txt`.
The direct yfinance requirement is pinned in `backend/requirements.txt`.

## Command and expected outcome

```sh
backend/.venv/bin/python -m backend.app.services.yahoo_stream \
  --symbol AAPL --timeout 60 \
  --output docs/evidence/part4-1/prompt05/aapl-subscription.json
```

The module opens yfinance's upstream connection and sends the AAPL subscription.
`subscription_sent` means sent, not a provider acknowledgement. It stops at the
first valid matching quote or the deadline (connection plus receipt share that
60-second budget). Cleanup has separate finite waits of up to five seconds each.
The CLI permits deadlines greater than zero and no longer than 120 seconds.
A missing quote, failed connection, or cleanup failure produces exit code 1;
a valid received quote with successful cleanup produces exit code 0.
A connection without a quote is explicitly receipt-unverified. Invalid messages
are counted and ignored rather than converted to market data.

## Actual observed result

The corrected run completed with **exit code 0** in **0.997 seconds**,
including cleanup. A genuine AAPL quote was received from
`wss://streamer.finance.yahoo.com/?version=2` through `yfinance.AsyncWebSocket`.
This was an actual network subscription, not a test fixture or chart-data poll.

| Field | Observed value |
| --- | --- |
| Connection opened, UTC | `2026-11-10T18:55:24.188424+00:00` |
| Symbol | `AAPL` |
| Price | `348.15` |
| Raw source timestamp | `1794336923000` |
| Source time, UTC | `2026-11-10T18:55:23+00:00` |
| Backend receipt time, UTC | `2026-11-10T18:55:24.992964+00:00` |
| Receipt minus source time | `1.992964` seconds |
| `day_volume` | `15781629` |
| `last_size` | `91` |
| Exchange field | `NMS` |
| Raw market-hours field | `1` |
| Cleanup | No error |

The full decoded source message and normalized fields are preserved in
`docs/evidence/part4-1/prompt05/aapl-subscription.json`; console output is in
`aapl-subscription.log`. The file also records the command, environment, timestamps,
subscription flags, and outcome. This verifies receipt of this quote, not every
trade, exchange completeness, guaranteed latency, or an indefinitely running feed.

## Timestamp and volume interpretation

- Yahoo's `time` was delivered as a protobuf int64 represented by a JSON string.
  It is Unix epoch **milliseconds**. Dividing the raw value by 1,000 produces the
  source UTC time above. The magnitude and contemporaneous receipt validate that
  interpretation; treating it as seconds would produce an invalid calendar date.
- Backend receipt is captured with a timezone-aware UTC clock at message-handler
  entry, before validation/conversion. It is distinct from source time. The raw
  source value is retained. The measured difference is observation age at receipt,
  not a certified measurement of exchange/network delay.
- `day_volume` is Yahoo's reported **cumulative day/session share-volume counter**,
  not the volume of this quote or a newly formed one-minute candle. It must not be
  summed across quote updates. Baselines, counter resets, deltas, and late-event
  handling belong to later prompts and are not implemented here.
- `last_size` is the reported last-trade size, not the cumulative counter. A quote
  update is not proof of a unique trade; summing repeated last sizes would also be
  unsafe. Missing volume fields remain absent, never silently zero-filled.
- The stream timestamp and volume fields are defined in the
  [pinned yfinance pricing schema](https://github.com/ranaroussi/yfinance/blob/0.2.66/yfinance/pricing.proto).
  The interpretation of timestamp units is additionally checked against the actual
  captured payload. See the
  [WebSocket client documentation](https://ranaroussi.github.io/yfinance/reference/yfinance.websocket.html).

## Tests, initial issue, and limits

```sh
backend/.venv/bin/python -m pytest backend/tests -q
```

Final result: **75 passed in 0.35 seconds**. The 17 new tests cover quote validation,
timestamps, cumulative/missing volume, stale labeling, successful receipt,
connection failure, no-message deadline, connection deadline, timeout validation,
and cancellation-before-close cleanup. Synthetic quotes are confined to tests.
The existing 58 backend tests, including polling behavior, also passed.
Raw output is retained in `docs/evidence/part4-1/prompt05/backend-tests.txt`.

The initial real attempt received a genuine quote, but its listener shutdown
stalled and was manually interrupted. That attempt's output is retained as
`initial-attempt.log`. Inspection of yfinance 0.2.66's listener showed that closing
the transport before cancelling the listener can loop on clean socket closure.
The check now cancels the listener before closing the transport. A regression
test verifies the cleanup order; the corrected real run completed in 0.997 seconds
with no cleanup error. No unresolved failure was observed in the final tests/run.

This evidence is limited to Prompt 05. The streaming check is callable from the
backend Python environment, but it does not replace the production dashboard's
polling worker or connect raw quotes to SQLite/charts. No other prompt is claimed
complete. Future checks during closed markets may produce no quote; those runs
must retain the connection flags and mark live receipt unverified.


## Official Prompt 06 — recording verification

Scope: raw decoded Yahoo quote recording and receipt journal only. No Prompt 07
candle formation, cumulative-volume arithmetic, higher-timeframe logic or Prompt
09 replay is implemented. The normal polling/dashboard paths remain intact.

### Commands and disposable database

```sh
# Automatic initialization and a bounded real capture into a separate file:
backend/.venv/bin/python -m backend.app.services.yahoo_stream \
  --symbol AAPL --timeout 60 --max-quotes 3 \
  --database /tmp/prompt06-capture.sqlite3 --output /tmp/prompt06-capture.json

# Repeatable constructed-data verification; creates its own disposable database:
PYTHONPATH=. backend/.venv/bin/python backend/tests/evidence/verify_raw_recording.py \
  docs/evidence/part4-1/prompt06

# All backend tests (mocked network; no new paid services/dependencies):
backend/.venv/bin/python -m pytest backend/tests -q
```

Default application/receiver path and the explicit initialization command are
recorded in README.md. Both normal FastAPI startup and the capture CLI initialize
the additive tables automatically. Existing candle rows are not migrated/replaced.

Actual constructed-data database path: `/var/folders/20/7d6ym4q54q907m_10zpjv1zw0000gn/T/prompt06-test-2guj86fj/disposable.sqlite3`.
The path is disposable; full expected/observed results and stored rows are retained
in `docs/evidence/part4-1/prompt06/disposable-recording.json` and `.txt`.
All input in that scenario is explicitly labeled **CONSTRUCTED TEST DATA**.

### Expected versus observed constructed inputs

| Case | Expected | Observed |
| --- | --- | --- |
| Original A then exact repeat A | One payload version; two receipts | Observation 1 reused at arrivals 1 and 2; second marked duplicate |
| Changed B at A's source time | Independent message, not an assumed correction | Observation 2, revision 0, no predecessor |
| Older C delivered after B | Later arrival sequence; older source timestamp | Observation 3 at arrival 4; original source time retained |
| Explicit TEST correction to B | Retain B; append linked version | Observation 4, revision 1, supersedes observation 2 |
| Repeat that correction | No new payload version; trace receipt | Arrival 6 points to observation 4, marked duplicate |
| Backend stopped; new interpreter starts FastAPI | All stored fields unchanged | Exact pre-restart journal equality confirmed |
| Another A after restart | Continue sequence, suppress repeated payload | Arrival 7, duplicate observation 1 |
| Integrity | SQLite integrity check succeeds | `ok` |

Observed durable rows before restart:

| Arrival | Observation | Source milliseconds | Price (TEST DATA) | Disposition | Revision | Supersedes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | 1794322800000 | 100 | inserted | 0 | — |
| 2 | 1 | 1794322800000 | 100 | duplicate | 0 | — |
| 3 | 2 | 1794322800000 | 101 | inserted | 0 | — |
| 4 | 3 | 1794322740000 | 99 | inserted | 0 | — |
| 5 | 4 | 1794322800000 | 102 | revision | 1 | 2 |
| 6 | 4 | 1794322800000 | 102 | duplicate | 1 | 2 |

There were **4 distinct stored versions and 6 receipts** before restart. The
new backend process reopened the same file with polling disabled, verified the
same rows, and appended receipt **7** without creating another version. Full
payloads, evidence, original source fields and all receipt timestamps are included
in the JSON artifact. The first backend lifespan was closed before the second
interpreter/process initialized a fresh FastAPI application.

Duplicates use canonical decoded JSON equality, source/ticker, and correction
context, not source time alone. JSON key ordering is ignored; source field types
and values are retained. A reader can traverse `arrival_sequence`, skip duplicate
receipts for processing, and apply explicitly linked revisions as replacements.
Different same-timestamp messages without proof remain independent observations.
The correction evidence used here was constructed; it is **not** a claim of an
actual Yahoo correction. The current adapter supplies no correction target.

### Genuine Yahoo recording check

The real WebSocket check opened a connection, received **3** valid AAPL
messages, and durably recorded arrival sequences **1, 2, 3** in
`/private/tmp/trading-dashboard-prompt06.1bVsb9/market.sqlite3`. It completed in
**3.303 seconds**, with capture target reached, no persistence error,
and no cleanup error. The first genuine quote price was **348.235**;
its source time was `2026-11-10T19:02:26+00:00` and handler receipt was
`2026-11-10T19:02:28.228322+00:00`.

A separate backend interpreter then reopened the file with external polling
disabled. It found all three journal rows, confirmed the first stored source
payload exactly equals the captured decoded Yahoo message, and confirmed the
receipt timestamp matches. Real rows (not constructed prices):

| Arrival | Observation | Source milliseconds | Price | Reported cumulative day volume |
| --- | --- | --- | --- | --- |
| 1 | 1 | 1794337346000 | 348.235 | 15947999 |
| 2 | 2 | 1794337346000 | 348.235 | 15948008 |
| 3 | 3 | 1794337349000 | 348.235 | 15948151 |

Artifacts: `live-recording.json`, `live-recording.log`, and
`live-database-check.json`, under `docs/evidence/part4-1/prompt06/`.
The source timestamp remains epoch milliseconds; reported day volume is retained
without converting it into per-trade/candle volume. No exchange-latency or
complete-trade-coverage guarantee is inferred from these messages.

### Automated results and remaining boundaries

Final backend suite: **96 passed in 0.68 seconds**, zero failures. Raw output and
JUnit results are retained as `prompt06/backend-tests.txt` and `backend-junit.xml`.
The 21 new recorder tests cover duplicate traceability, same-time independence,
explicit correction chains/reversions, late arrivals, equal/backward receipt
clocks, concurrent deduplication, process restart/continued sequencing, legacy
candle-schema preservation, malformed data, receiver integration and persistence
failure reporting. Existing polling and Prompt 05 checks also pass. Frontend code
was not changed, so frontend tests/build were not rerun for this backend-only step.

An initial integration assertion expected three callbacks from the original
one-quote verifier and failed. The recorder worked for callbacks accepted before
shutdown. An explicit bounded `--max-quotes` target and post-target callback guard
were added; the integration check and complete final suite now pass. The three
real receipts above verify the final behavior rather than that initial assertion.

SQLite recording is synchronous within the sequential callback so a cancelled
capture does not leave an untracked write running in a worker thread. A lock may
wait up to the existing 10-second busy timeout; this can extend the network
capture deadline. A partial capture is retained and reported separately from a
complete target; persistence failure gives a non-success outcome.

Remaining: Prompt 07 must calculate candles/volume from these observations;
Prompt 09 must consume this journal in arrival order. Neither is implemented.
The existing FastAPI background worker still serves polled candle history; raw
recording runs while the bounded upstream receiver is active. This is not an
always-on new streaming daemon. Invalid/control messages are ignored and counted,
not inserted as accepted market observations. No database recording can establish
a Yahoo correction relationship that the source itself does not identify.


## Official Prompt 07 — one-minute candle verification

Scope: one-minute Python calculation from saved raw observations only. No Prompt
07A higher timeframes, new chart delivery, or replay player. All instructor example
prices below are **CONSTRUCTED TEST DATA**, confined to test/evidence code.

### Exact official input and expected result

Times are UTC on November 9, 2026; volume is the running session total:

| Source time | Price (TEST DATA) | Cumulative volume |
| --- | --- | --- |
| 14:59:59 | 99 | 1000 |
| 15:00:05 | 100 | 1004 |
| 15:00:20 | 103 | 1009 |
| 15:00:50 | 101 | 1011 |

Expected candle: `timestamp=2026-11-09T15:00:00Z`, open 100, high 103, low 100,
close 101, volume **11**. Baseline is the preceding observation's 1000; differences
are 4 + 5 + 2, not the sum of cumulative counters. The 14:59 baseline observation
also forms its own earlier candle with unknown volume; it is not included in the
15:00 OHLC prices.

### Measured comparisons

| Scenario / calculation time UTC | Expected | Observed |
| --- | --- | --- |
| All four receipts available; 15:00:59 | 100 / 103 / 100 / 101 / 11; forming | Exact match; `is_complete=false` |
| Same saved records; 15:01:00 | Same OHLCV; complete | Exact match; `is_complete=true` |
| Middle source observation not yet received; 15:01:00 | 100 / 101 / 100 / 101 / 11 | Exact match |
| Middle source 15:00:20 arrives at 15:01:10; calculate at 15:01:11 | 100 / 103 / 100 / 101 / 11; complete | Exact match; high revised, volume not double-counted |
| No preceding cumulative baseline | Volume unknown | `volume=null`, missing baseline reason |
| Counter decreases from 1004 to 3 | Affected minute volume unknown | `volume=null`, reset/regression reason |
| Unchanged counter with valid baseline | Known zero | `volume=0`, volume status known |
| Repeated calculation | Same result; no journal changes | Exact equality; source journal unchanged |

Before the late message arrives, total volume is already inferable from cumulative
endpoints but the missing price high is not. This is not treated as full trade
coverage. Recomputing an earlier as-of time after the late record was saved still
returns the earlier state, without leaking that later receipt.

Actual candle payload before completion:

```json
{
  "timestamp": "2026-11-09T15:00:00Z",
  "open": 100,
  "high": 103,
  "low": 100,
  "close": 101,
  "volume": 11
}
```

Completion and volume status are separate metadata rather than extra candle
fields. The same six-field payload appears after completion, with complete state
in metadata. Completion means interval elapsed, not immutable/final or full coverage.

### Reproduction and evidence

```sh
PYTHONPATH=. backend/.venv/bin/python backend/tests/evidence/verify_minute_candles.py \
  docs/evidence/part4-1/prompt07

backend/.venv/bin/python -m backend.app.services.minute_candles \
  --database /path/to/the/generated/candles.sqlite3 \
  --as-of 2026-11-09T15:01:00Z --output /tmp/prompt07-candles.json

backend/.venv/bin/python -m pytest backend/tests -q
```

The actual disposable exact-example database was `/var/folders/20/7d6ym4q54q907m_10zpjv1zw0000gn/T/prompt07-test-jvdjf6c8/candles.sqlite3`.
Separate temporary files hold late-arrival, missing-baseline and reset inputs.
Full expected/observed objects, per-candle metadata and input receipts are in
`docs/evidence/part4-1/prompt07/instructor-example.json`; console output is in
`instructor-example.txt`. The calculator CLI was also invoked against that saved
database and matched the expected candle exactly (`cli-calculation.json/.txt`).
Source rows were unchanged by the calculations; no raw or polling-history table
was written by the calculator.

Final automated result: **121 passed in 0.79 seconds**, zero failures. The 25
new calculation tests cover exact instructor output, forming/completion boundaries,
late source-order open/close, duplicate receipts, identified corrections and their
next-minute volume effects, missing/invalid baselines, counter resets/recovery,
known zero, same-timestamp ambiguity/tie-breaking, gaps, New York date partition,
read-only repeatability/reopen, empty input, validation, receipt-clock regression,
and corrections that move source time between minutes. Existing 96 backend tests
also passed, preserving polling, candle history, recording and subscription behavior.
Raw test output and JUnit are retained in `prompt07/backend-tests.txt` and
`backend-junit.xml`. No frontend files changed, so frontend tests/build were not
rerun for this backend calculation change. No dependencies were installed.

### Genuine saved observations, separate from the constructed example

The existing Prompt 06 disposable database was still available, so the calculator
was also run read-only against those three genuine saved Yahoo observations. It
returned the 19:02 UTC candle on November 10 with O/H/L/C all **348.235**, three
effective observations, and **volume null**. No pre-minute baseline was captured,
and different cumulative counters shared the same source timestamp; both reasons
are reported instead of inventing a volume. This is saved historical source data,
not a new live-feed test. Exact output and command are retained in
`prompt07/saved-yahoo-calculation.json/.txt`. No Yahoo network request was made in
this step. The instructor's synthetic OHLCV example is not labeled genuine data.

### Rules and remaining scope

Source times determine OHLC order; arrival sequence is only a stable tie-break
where source timestamps coincide. Corrections retain the predecessor's original
tie position and replace it at receipt time. Cumulative volume is conservatively
unknown for absent/invalid counters, reset/regression, conflicting same-time
counters, missing-minute gaps, or invalid session baselines. Reliable subsequent
observations can establish a baseline for later minutes; partial totals are never
presented as a complete known minute volume. Volume is derived from observed
counter changes, not a guarantee of every exchange trade.

The calculator uses an as-of arrival prefix, so later receipts/corrections do not
change earlier snapshots, even when receipt clocks move backward. This prepares
calculation for later integration but is not a Prompt 09 timed replay mechanism.
AAPL's New York date/exchange partitions the daily counter; no exchange-calendar
or larger-timeframe implementation was added. Prompt 07A is still pending, as
are chart integration of these new raw-derived candles and recorded-session replay.
The Vue dashboard and existing polling/history routes remain unchanged.


## Official Prompt 07A — higher-timeframe verification

Scope: derive request-time history for 1m, 5m, 15m, 1h and 1d from the same resolved
one-minute data. No Prompt 08 chart selector/live aggregated delivery or Prompt 09
playback was implemented. The existing dashboard, WebSocket and polling history
remain in place. No paid API was called and no new Yahoo data was fetched.

### Calendar dependency and compatibility

The free `pandas_market_calendars` library was installed in the existing
Python 3.9.6 `backend/.venv`. Initial version 5.2.4 advertised a compatible Python
requirement but its module import raised a TypeError on a Python-3.10 union
annotation. It was replaced by **4.6.1**, which imported and calculated schedules
successfully. The final runtime requirement is pinned to that version:

```sh
backend/.venv/bin/python -m pip install 'pandas_market_calendars==4.6.1'
```

Resolved `exchange-calendars` is 4.5.6, pandas remains 2.3.3, and NumPy remains
2.0.2. `pip check` reported no broken requirements. The calendar package uses its
shared NYSE US-equity schedule for the NASDAQ alias. Actual 2026 holiday/early-close
outputs were compared with [Nasdaq's official schedule](https://nasdaqtrader.com/Trader.aspx?id=Calendar).
The package is an offline rule source, not a live exchange-halt/announcement feed.
Future calendar changes need maintenance rather than a claim of guaranteed accuracy.

### Instructor example: expected versus observed

The supplied `live_market_workstation/prompts/data/sample-candles.csv` contains
three **CONSTRUCTED INSTRUCTOR TEST DATA** minutes at 14:30, 14:31, and 14:32 UTC on
November 9, 2026. They have volumes 1200, 850, and 1500. Expected five-minute candle:

```json
{
  "timestamp": "2026-11-09T14:30:00Z",
  "open": 100,
  "high": 105,
  "low": 99,
  "close": 104,
  "volume": 3550
}
```

| Check | Expected | Observed |
| --- | --- | --- |
| 5m OHLCV | 100 / 105 / 99 / 104 / 3550 at 14:30 UTC | Exact match |
| 5m coverage | Partial, 3 of 5 minutes | Available 3, expected 5, missing 2 |
| Daily timestamp/OHLCV | Same available data, timestamp at 14:30 session open | Exact match |
| Daily coverage | Partial, 3 of 390 minutes | Available 3, expected 390, missing 387 |
| Add 14:35 minute | Second 5m group, same daily group | 14:30 and 14:35 buckets; daily available count 4 |
| Add next trading session, Nov 10 at 14:30 | Separate daily candle | Two daily candles, one per session |
| Volume unknown vs zero | Null remains unknown; zero remains known | Both distinct; both can have partial coverage |
| Completed interval with gaps | Complete clock interval, partial data | Both statuses retained independently |
| History API raw calculation | Same example derived from saved one-minute results | Exact match for 5m and daily; all five intervals returned successfully |
| Read-only derivation | Raw journal unchanged after requests | Exact before/after equality |

Known volume is the sum of the **available** minute volumes. For the partial 5m
interval, 3550 is not asserted to include trades from the two missing minutes.
Missing minutes are not synthesized as zero. Unknown input minute volume propagates
null. Six candle fields are retained; coverage, completion and volume provenance
are separate metadata.

### Calendar, boundary and as-of checks

| Scenario | Expected and observed result |
| --- | --- |
| March 6 → March 9 DST transition | Open changes 14:30 → 13:30 UTC; close 21:00 → 20:00 UTC |
| October 30 → November 2 DST transition | Open changes 13:30 → 14:30 UTC; close 20:00 → 21:00 UTC |
| Nov 26 Thanksgiving | No regular session; input holiday minutes excluded |
| Nov 27 and Dec 24 early close | Open 14:30 UTC, close 18:00 UTC; daily denominator 210 |
| Final early-close hourly interval | Start 17:30 UTC, end 18:00 UTC, expected 30 minutes |
| Summer hourly anchor | 14:10 UTC minute belongs to 13:30 UTC hourly bucket, not 14:00 |
| Exact 1m/5m/15m/1h boundaries | Next minute at 14:31/14:35/14:45/15:30 starts a new corresponding bucket |
| Regular close timestamp | Close-exclusive: a 16:00 New York bar is not a new regular-session minute |
| All ten 2026 full-day holidays and weekend | Excluded by calendar |
| Intraday gaps | Stay within same daily session; no generated bars |
| Future source timestamp or future availability | Excluded at earlier displayed clock |
| Future base snapshot used for past clock | Rejected; caller must recalculate base snapshot at that clock |
| Late third-minute receipts | Earlier snapshot has two minutes; after arrival gets the complete supplied three-minute example |
| Identified minute high correction | Only appears after its receipt; replaces old price without adding a minute or volume |
| Polling history correction | Earlier as-of uses revision 0, later uses revision 1; latest-state view does not leak future correction |

The raw history test creates labeled test observations whose Prompt 07 one-minute
results match the instructor CSV, so the HTTP check exercises saved raw data →
minute calculation → larger aggregation rather than only aggregating a list in
memory. Polling-dataset and raw-dataset evidence are never combined or presented
as a real live capture. All constructed fixtures remain under tests/evidence.

### Commands, paths, and measured results

```sh
PYTHONPATH=. backend/.venv/bin/python backend/tests/evidence/verify_timeframes.py \
  docs/evidence/part4-1/prompt07a

backend/.venv/bin/python -m pytest backend/tests -q
# From frontend/:
npm test
npm run build
```

The actual disposable API-test database was `/var/folders/20/7d6ym4q54q907m_10zpjv1zw0000gn/T/prompt07a-test-yuuq5l30/raw.sqlite3`.
Expected/observed candle and metadata payloads for every timeframe are in
`docs/evidence/part4-1/prompt07a/instructor-example.json`, with command output in
`instructor-example.txt`. The artifact also records DST, early-close and boundary
results, zero/unknown volume comparisons and journal preservation.

Final results:

- **166 backend tests passed in 1.50 seconds**, zero failures; 45 new Prompt 07A
  cases plus all 121 existing backend cases. Raw output/JUnit are retained as
  `prompt07a/backend-tests.txt` and `backend-junit.xml`.
- **29 frontend tests passed** after adding the same-origin `/history` proxy;
  output/JUnit in `frontend-tests.txt` and `frontend-junit.xml`.
- **Production build passed** in 336 ms; output in `frontend-build.txt`.
- Final dependency versions are retained in `installed-dependencies.txt`.

The initial calendar import failed as described above; the compatible pinned
version resolved it. No final functional/test failure remains. Existing polling,
raw storage, source-time candle calculation, chart client and original HTTP/WS
contracts continue to pass their checks.

### Current API and remaining scope

`GET /history?symbol=AAPL&timeframe=5m&dataset=raw&as_of=<aware ISO timestamp>`
accepts `1m` (default), `5m`, `15m`, `1h`, `1d`. The default dataset is `raw`;
`dataset=polling` explicitly selects saved Yahoo chart candles. Empty input gives
empty arrays. The old `/observations` route is preserved, not redefined to a new
shape. Invalid symbols/timeframes/datasets or naive clocks return 422; unavailable
SQLite returns 503. Source/dataset provenance is returned. This API performs no
new market-data fetch and no database writes.

Prompt 08 remains: connect these history intervals and forming/revised aggregated
updates to the Vue chart and add a selector. Prompt 09 remains: actual timed,
arrival-order session replay. Request-time `as_of` filtering is safety preparation,
not a claim that either later prompt is complete. Only AAPL's regular equity
session is configured, and static calendar rules are not real-time market-status
or full exchange-trade-coverage verification. No report, commit or push was created.

## Prompt 08 — persistent subscription and visible derived candles

Executed November 10, 2026 during the regular session, approximately 20:47–20:55
UTC. Read official `08-display-live-updates.md`, project instructions, existing
subscription, SQLite recorder, calculators and Vue chart. `design-pipeline.md` did
not previously exist; it now documents the integrated pipeline. No dependency
installation was required. The existing pinned yfinance 0.2.66 environment was used.

### Expected versus observed

| Check | Expected | Observed |
| --- | --- | --- |
| Real upstream | Receive an actual AAPL quote through Yahoo WebSocket | PASS: raw quote 349.44, source 2026-11-10T20:49:02Z, durable arrival 96; `live-wire-trace.json` includes original fields and backend receipt |
| Durable before delivery | SQLite arrival exists before derived message is received | PASS: arrival/payload/source time matched; entire wire candle and metadata snapshot equaled an independent SQLite calculation at its as-of clock |
| Visible chart trace | Visible time/OHLC correspond to committed raw inputs | PASS: captured DOM arrival 229, source milliseconds 1794343862000 (20:51:02Z), backend receipt 20:51:03.511861Z, raw price 349.805. Visible 20:51 candle O=349.78 H=349.81 L=349.77 C=349.81 (display rounding); all five ledger rows matched independent SQLite aggregation |
| Forming and next interval | Update last bar and append next interval | PASS: genuine screenshots/ledger include forming 20:51 bar and elapsed 20:50 bar; recovered chart includes new 20:52 bar. Unit tests check in-place updates, old corrections and aggregation boundaries |
| Volume honesty | Unknown without valid cumulative baseline | PASS: genuine raw minute volumes were null and displayed Unknown, histogram whitespace; no synthetic zero or invented trade volume |
| Upstream disconnect | Browser can remain connected while upstream fails | PASS after fallback fix below: header Connected; upstream unavailable/socket closed; “Upstream unavailable · polling fallback”, HTTP poll → WebSocket, delayed/polled label |
| Upstream recovery | Retry subscription, persist new quotes, return to raw | PASS: after clearing test interruption, UI available/socket open, Yahoo WS → FastAPI WS, genuine newest source 20:52:40Z, backend receipt 20:52:41Z, current raw candle close 349.70 |
| Timeframes | Load matching history and live subscription for all five frames | PASS: browser selection requests all five frame-specific HTTP/WS URLs (server log); five-frame automated HTTP/WS comparisons pass; raw daily screenshot shows Daily/forming/unknown volume and partial session coverage |
| Duplicates and corrections | Retain arrival trace, no double-counting, revise candles | PASS with explicitly constructed tests; genuine provider correction was not observed or claimed |
| Safe shutdown | Stop shared receiver and close its connection | PASS in automated reconnect/shutdown test and normal evidence-server shutdown |

### Failure discovered and corrected

The first controlled upstream outage made browser delivery fail: history returned
422 (“Base candles must start on a UTC minute boundary”). The real Yahoo chart
response included an instantaneous final quote timestamp rather than a minute bar.
The aggregation adapter now excludes non-minute rows while retaining them in the
legacy SQLite history. A regression test checks both exclusion and preservation.
The outage was repeated successfully after restarting the backend with this fix.
The server log deliberately retains the initial failure and marked test-only
connection errors; they are not unreported successful checks.

The interruption used `backend/tests/evidence/prompt08_server.py`, a test-only
connection subclass controlled by `/tmp/trading-dashboard-prompt08-control.json`.
It throws an explicitly labeled connection error but supplies **no fake prices**.
Polling prices and recovered WebSocket quotes were genuine Yahoo responses. No
production test-control endpoint was added. The disposable database was
`/tmp/prompt08-live-20261110.sqlite3`; the application's default database was not used.

### Evidence and reproducibility

Evidence directory: `docs/evidence/part4-1/prompt08/`.

- `live-wire-trace.json` / `.txt`: original decoded payload, persisted row, complete
  derived wire state, UTC clocks and assertions.
- `visible-live-dom.json`, `visible-sqlite-comparison.json`, `live-1m.jpg`: visible
  chart/ledger and independent as-of SQLite comparison. Screenshot capture follows
  DOM capture during an active feed, so the newest source timestamp can advance.
- `upstream-outage.jpg` / `-dom.txt`: corrected fallback while browser stays connected.
- `upstream-recovered.jpg` / `-dom.txt`: genuine raw recovery.
- `daily-raw.jpg` / `-dom.txt`: raw daily aggregation; `daily-polling.*` separately
  records polling daily history. `daily-loading.jpg` records the transient loading
  state and is not used as proof of completed history loading.
- `timeframe-switches.json` records browser state snapshots. Its convenience string
  check for “1 day” is false because the actual UI label is “Daily”; subsequent
  daily DOM/screenshot and all-frame endpoint tests verify the actual result.
- Backend/frontend server logs, automated-test text/JUnit, production-build output.

Reproduce wire trace while the local evidence servers are running:

```sh
PYTHONPATH=. backend/.venv/bin/python backend/tests/evidence/verify_live_chart.py \
  /tmp/prompt08-live-20261110.sqlite3 docs/evidence/part4-1/prompt08/live-wire-trace.json
PYTHONPATH=. backend/.venv/bin/python backend/tests/evidence/verify_prompt08_dom.py \
  /tmp/prompt08-live-20261110.sqlite3 docs/evidence/part4-1/prompt08
```

The first command is bounded and fails explicitly if no genuine matching quote is
received. The second compares the previously captured visible DOM at its own
snapshot clock. Display prices use decimal rounding to two places; comparisons
account for this instead of Python's different default tie rounding.

### Final automated validation and remaining limits

- Backend: **178 passed**, including all prior raw storage, revision, source-time
  candle, calendar/timeframe and polling tests plus persistent-receiver integration.
- Frontend: **34 passed**, covering client reconnect, timeframe callback isolation,
  stale HTTP response rejection, source replacement, raw age, status separation,
  forming state, older revisions and unknown-volume chart behavior.
- Production build: **passed**. Exact output and durations are in the evidence files.

The real visual check passed; it is not pending. This verifies receipt and display,
not exchange latency, complete trade coverage, or correct attribution of a genuine
minute's volume. Yahoo sends quotes over a true upstream WebSocket; the preserved
fallback remains HTTP polling every 30 seconds. The app never guarantees either
source is real-time. The adapter depends on two private pinned-yfinance members;
future upgrades require re-verification. Reconnects cannot recover unreceived raw
observations. Complete intervals can remain partially covered and revisable.

Prompt 09 timed arrival-order replay remains unimplemented. Automated corrections
and failure fixtures are labeled as constructed; no real provider revision was
claimed. No report.md, commit, push or paid API call was made.

## Prompt 09 — read-only recorded-observation replay

Executed November 10, 2026, approximately 22:32–22:39 UTC, after the regular market
session. Read official `09-replay-recorded-observations.md`, AGENTS.md and the
existing pipeline, raw journal, source-time calculator and Vue chart. No dependency
installation or paid service was needed. Prompt 09 changes add replay only; live
Yahoo WebSocket delivery and the HTTP polling fallback remain available.

### Offline genuine-session verification

Copied the genuine Prompt 08 SQLite session with SQLite's backup API to disposable
`/tmp/prompt09-replay.sqlite3`. Started the normal production backend with
`DATABASE_PATH` pointing to that copy and **both** `YAHOO_STREAM_ENABLED=false`
and `YAHOO_FEED_ENABLED=false`. The Vue dashboard used the same-origin proxy.
No replay test-control routes, fake prices or external Yahoo requests were used.
The owner’s default database was not modified.

Selected AAPL backend receipt range **2026-11-10T20:50:00Z through
20:50:59.999999Z**, inclusive. It contains **69 arrivals**, ending at durable
arrival **224**. The first received source observation belongs to 20:49:59, so the
visible selection correctly also touches the preceding 20:49 candle. Earlier
journal context supplies prior OHLC values within touched candles and volume
baselines; the receipt range does not invent truncated source-time bars.

| Check | Expected | Observed |
| --- | --- | --- |
| Offline playback | Operate without Yahoo upstream | PASS: both workers disabled; UI shows Replay / AAPL, Replay · recorded data, SQLite arrival replay, upstream not required/socket closed |
| Source versus arrival order | Late source data appears at its receipt sequence | PASS: API advances durable sequence; same-receipt fixtures do not leak later arrivals; source times are unchanged |
| Genuine minute OHLC | Independently derive from saved SQL records | PASS: 20:50 minute O=349.70, H=349.90, L=349.62, C=349.80; identical visible chart and ledger |
| Genuine minute volume | Preserve uncertainty in saved data | PASS: volume null / Unknown. SQL shows multiple independent records with the same source timestamp but different cumulative counters; e.g. 20:50:00 has 19,859,039 then 19,869,321. No correction identity justifies collapsing these messages, so volume attribution is ambiguous |
| Replay clock | Clock follows saved receipt, not current wall time | PASS: final clock 2026-11-10T20:50:59.503964Z, source 20:50:59Z; playback ends with the minute still forming |
| Two API runs | Identical complete state at every step | PASS: all 69 frame objects equal, including candle metadata and clocks; per-frame hashes saved |
| Two visible runs | Identical final rows, cursor and clock | PASS: both completed in browser at 69/69; independent DOM comparison matched the SQL-verified API state |
| Counts / records | No stored observation changes | PASS: raw rows 454 → 454; arrival journal 454 → 454; polling candle versions 1,958 → 1,958; full-record SHA-256 unchanged |
| Timeframe switching | Recalculate same cursor, no future advance | PASS: browser 5m screenshot retains arrival 224 and replay clock; automated API/UI tests check no-advance calculation |
| Empty selection | Clear message, no stale candles | PASS: next-day empty range shows 0/0 and “No recorded observations in this selection.”; chart cleared |
| Return to live dashboard | Existing cached chart and status remain usable | PASS: normal dashboard restored saved polling bars and closed-market/paused status |

The full-record hash before and after two API runs was
`70dc9aa79bb0d16a30cf58c97cba2bc76a6bbfd66d8f86fabe2b1ba14d239218`.
The browser repeats and empty-selection checks also ran against the same feed-off
copy. The evidence verification command was repeated after browser checks to
confirm the saved record hash/counts still matched.

### Constructed edge-case tests (not genuine provider corrections)

`backend/tests/test_replay.py` uses a separate disposable SQLite database. Its
pre-range cumulative baseline is 1,000; received prices/counters are 100/1,004,
101/1,011, then late 103/1,009. At the late arrival step the expected and observed
minute is **O=100 H=103 L=100 C=101 V=11**. An explicit correction changes the middle
price to 104 only at the correction's arrival; the earlier frame remains unchanged.
An exact duplicate advances the arrival trace without increasing volume. A next-
minute arrival changes completion. Repeating all frames gives identical objects
and unchanged saved records. Additional checks cover frozen high-water exclusion
of newly appended records, same-time receipts, backward receipt-clock adjustment,
empty ranges, invalid cursors, unsupported intervals/symbols and naive clocks.

The real session contained no identifiable provider correction; correction behavior
is verified by those labeled fixtures rather than claimed as observed live behavior.

### Automated tests and saved evidence

- **Backend: 182 passed** (all 178 prior tests plus four replay tests).
- **Frontend: 40 passed** (all 34 prior tests plus replay controls and mode-isolation
  tests): load/restart, stepping/frozen cursor, automatic playback, timeframe
  recalculation, empty result, stale-range response rejection, validation errors,
  and incoming live updates isolated from the replay chart.
- **Production build: passed**, 347 ms in the final recorded run.

Outputs are under `docs/evidence/part4-1/prompt09/`: backend/frontend text and JUnit,
build output, backend-offline/frontend logs, `real-replay-comparison.json` with
SQL-derived expectations and saved counter examples, two replay screenshots/DOM
captures, `visible-replay-comparison.json`, 5m screenshot/DOM and empty-range proof.

The first browser attempt exposed the missing `/replay` Vite proxy (HTML returned
instead of API JSON). Adding the same-origin proxy resolved it; all subsequent
browser/API checks passed. An initial test compared equivalent ISO strings with
and without fractional seconds; it now compares parsed UTC instants. The first
independent volume check looked for a counter regression; inspecting actual SQL
records established same-timestamp counter ambiguity instead, and the final
verification explicitly checks that observed condition. No unobserved volume
behavior is claimed.

Reproduce the evidence with the offline backend and Vue running:

```sh
PYTHONPATH=. backend/.venv/bin/python backend/tests/evidence/verify_replay.py \
  /tmp/prompt09-replay.sqlite3 docs/evidence/part4-1/prompt09/real-replay-comparison.json
backend/.venv/bin/python backend/tests/evidence/verify_replay_dom.py \
  docs/evidence/part4-1/prompt09
```

### Remaining limitations

No final test failure or required Prompt 09 check remains pending. Playback uses
fixed 300 ms steps plus request time, preserving receipt order rather than original
gaps. Only saved raw AAPL sessions are offered; polling-only history cannot recreate
raw arrivals. Prefix recalculation per step is suitable for this assignment's small
recordings, not an optimized long-session replay engine. Unknown volume and partial
coverage remain honest limitations of the captured source. Replay requires the local
backend, even though Yahoo can be disconnected. No report.md, commit, push or paid
API call was made. Submission/report writing remains with the project owner.
