# Part 4.1 evidence index

## Final evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Yahoo WebSocket to visible chart | Genuine AAPL quote matched the SQLite-derived candle and browser display | [Live trace](prompt08/live-wire-trace.json), [chart comparison](prompt08/visible-sqlite-comparison.json) |
| Upstream outage and recovery | Simulated interruption activated the labeled polling fallback. Streaming resumed after recovery. | [Outage](prompt08/upstream-outage.jpg), [recovery](prompt08/upstream-recovered.jpg) |
| Read-only SQLite replay | Two runs of 69 genuine arrivals matched, with unchanged record counts and hashes | [SQL and replay comparison](prompt09/real-replay-comparison.json), [visible comparison](prompt09/visible-replay-comparison.json) |
| Final automated tests and build | 182 backend tests and 40 frontend tests passed. Production build passed. | [Backend](prompt09/backend-tests.txt), [frontend](prompt09/frontend-tests.txt), [build](prompt09/frontend-build.txt) |
| Demonstration video | 53.7 seconds | [final-demo.mov](final-demo.mov) |

See [verification](../../verification.md) for methods and [pipeline design](../../design-pipeline.md)
for the current architecture. AAPL candles cover the regular session. Yahoo
latency and trade coverage are unverified, and ambiguous volume is displayed as
unknown. Revision behavior was tested with constructed inputs.

## Early polling milestone

The following evidence records the November 8, 2026 polling milestone
(America/New_York). These counts and screenshots describe that run.

| Check | Outcome | Artifact |
| --- | --- | --- |
| Backend automated suite | 58 passed, 0 failures/errors/skips | `backend-tests.txt`, `backend-junit.xml` |
| Frontend automated suite | 29 passed, 0 failures/errors | `frontend-tests.txt`, `frontend-junit.xml` |
| Production frontend build | Exit 0 | `production-build.txt` |
| Real Yahoo → FastAPI → SQLite → WebSocket | Pass: 1,951 stored revisions; 390-candle snapshot matches HTTP and SQLite | `live-flow.json`, `live-flow.txt` |
| Default periodic polling | Pass: successful receipts at 02:53:44.236452 and 02:54:14.578531 UTC (~30.342 s apart) | `live-flow.json` status samples |
| Persistence across backend process restart | Pass: unchanged OHLCV, event/receipt times, revisions and row count | `cached-restart.json`, `persistence-comparison.json` |
| Automatic browser reconnect and fresh-page history | Pass: connected again; saved candles restored with polling disabled | screenshots 02–04 and matching DOM files |
| Mock Yahoo HTTP 503 | Pass: unavailable status, cached candles retained | `unavailable-feed.json`, screenshot 05, failure server log |
| Remove failure and recover | Pass: available, failure count 0, poll delay 30 s | `feed-recovered.json`, screenshot 06 |
| Controlled real-candle append | Pass: 1 → 5 browser candles, 4 actual WebSocket additions | `controlled-replay.json`, screenshots 07–09 |
| Duplicate replay | Pass: 5 total rows, no extra observation broadcast | `controlled-replay.json` |
| Reverse-order candles and mocked revisions | Pass: chronological reads, highest revision retained, 7 expected rows | `observation-cases.json`; backend suite |
| Responsive mobile | Pass: 390 px viewport/document width, no page overflow | `mobile-geometry.json`, screenshot 10 |

`check-results.json` records commands, working directories, actual UTC execution
times, and exit codes. `browser-verification.json` retains actual rendered state,
ledger values, and captured console output. The post-replay console capture
contained no warnings/errors. Expected connection failures during deliberately
stopped servers remain visible in the Vite/server logs.

One browser automation exact-text selector did not match the candle count because
it was embedded in a larger text node. The actual DOM showed one candle. Verification
continued using the observed chart-footer text; the one-to-five append then passed.
This was a verification selector failure, not an application failure. No failed
functional check was observed in this run.

## Screenshots and provenance

1. `01-yahoo-closed-market.jpg`: production app, actual Yahoo data; both price and
   volume chart panes; closed market and stale labels. Source time is Nov 6, 21:00 UTC.
2. `02-backend-disconnected.jpg`: stopped backend, retained real candles.
3. `03-auto-reconnected-saved-history.jpg`: browser automatically reconnects after
   backend process restart with external polling disabled.
4. `04-fresh-page-saved-history.jpg`: page reload retrieves persisted observations
   without Yahoo access; polling paused and market metadata unknown are expected.
5. `05-mocked-yahoo-unavailable.jpg`: **mocked HTTP 503**, real cached candles;
   unavailable status is distinct from browser connection status.
6. `06-feed-recovered.jpg`: real provider restored after removing mock failure.
7. `07-replay-before-one-real-candle.jpg`: controlled replay baseline, one real bar.
8. `08-replay-after-five-real-candles.jpg`: same page after four additional real bars.
9. `09-replay-chart-detail.jpg`: viewport detail of price/volume after replay.
10. `10-mobile-real-history.jpg`: 390 px responsive view of the historical replay.

Replay observations are the five real records preserved in `live-flow.json`.
Their prices, volume, event times, and original receipt times were not modified.
The replay is **not new market activity**. Repository revision checks alter only
version/completion metadata and explicitly document that simulation. Automated
unit suites also use their existing synthetic test fixtures, confined to tests.

## Reproduce the early polling checks

Run normal backend/frontend using the root README. To capture the normal stack:

```sh
DATABASE_PATH=/tmp/part4-1-verification.sqlite3 YAHOO_STREAM_ENABLED=false \
  backend/.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
# Separate terminal: cd frontend && npm run dev
# Separate terminal, from repository root:
backend/.venv/bin/python backend/tests/evidence/check_running.py \
  --database /tmp/part4-1-verification.sqlite3 --scenario real-yahoo-default-polling \
  --wait-poll --output /tmp/new-live-flow.json
```

Use fresh temporary database files for a new run and retain these original artifacts.
For cached restart, stop the backend and restart with the same database plus
both `YAHOO_STREAM_ENABLED=false` and `YAHOO_FEED_ENABLED=false`. The verification script also works without `--wait-poll`
for disabled/unavailable scenarios. Failure/replay entry point:

```sh
# Put {"mode":"failure"}, {"mode":"real"}, or {"mode":"replay","count":1}
# into /tmp/part4-1-feed-mode.json before starting.
DATABASE_PATH=/tmp/part4-1-replay.sqlite3 \
EVIDENCE_MODE_PATH=/tmp/part4-1-feed-mode.json \
EVIDENCE_CAPTURE_PATH=docs/evidence/part4-1/live-flow.json \
  backend/.venv/bin/python -m uvicorn backend.tests.evidence.server:app \
  --host 127.0.0.1 --port 8000
```

The replay script `backend/tests/evidence/check_replay.py` expects this dedicated
replay database to contain exactly the initial one-candle replay and the Vue
proxy on port 5173. It changes count to five, invokes the test-only poll endpoint,
and asserts the actual messages and SQLite rows. It writes evidence; copy/adapt it
to another output directory before rerunning if preserving this run's files.
Never expose this test-only app publicly. The production entry point remains
`backend.app.main:app` and has no evidence-control routes.
