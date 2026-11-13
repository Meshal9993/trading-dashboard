# Agent instructions

## Project context

Trading Dashboard for IST 402, Assignment 4, Part 4.1. The selected stack is
Vue 3, FastAPI, SQLite, WebSockets, and Yahoo Finance.

## Authorized scope

- Official Prompt 05 is authorized: approved yfinance 0.2.66 in the existing
  backend environment, bounded AAPL WebSocket verification, tests, and documentation.
- Official Prompt 06 is authorized: decoded raw-message persistence, durable
  duplicate-aware arrival journal, explicit correction links, tests and schema docs.
- Official Prompt 07 is authorized: one-minute candles calculated read-only from
  saved raw observations, cumulative-volume rules, completion status, tests and docs.
- Official Prompt 07A is authorized: regular-session calendar-aware aggregation
  for 1m/5m/15m/1h/1d, timeframe history API, as-of safety, tests and documentation.
- Preserve candle history and polling. Prompts 08 and 09 are now authorized.
- Keep coverage/completion separate; do not treat missing minutes as known zero.
- Keep unknown candle volume as null and completion metadata outside the six fields.
- Never infer raw-message corrections solely from matching source timestamps.
- Preserve all accepted receipt events and their durable arrival sequence.

- Step 5 covers end-to-end verification and submission evidence for the existing
  Vue/FastAPI/SQLite/WebSocket dashboard.
- Save actual screenshots and measured test results under `docs/evidence/part4-1/`.
- Clearly distinguish real Yahoo data, controlled historical replay, mocked failures,
  and mocked revisions. Never claim unobserved live-market behavior as verified.
- Do not create `report.md`; the project owner will write the report.
- Test-only evidence control routes must never be added to the production app.
- Maintain the existing free Yahoo polling and FastAPI/SQLite backend.
- Never label polled, delayed, or historical data as real-time.
- Yahoo chart data has no source revision counter; local snapshot revisions must
  be identified as locally generated, not provider sequence numbers.
- Do not use paid APIs.
- Do not commit or push without explicit approval from the project owner.
- Preserve existing work and remote history; never force-push without authorization.

## Conventions

- Keep Vue code under `frontend/` and FastAPI code under `backend/`.
- Keep database access in `backend/app/db/` and validated models in `backend/app/schemas/`.
- Put future Yahoo Finance integration and business logic in backend services.
- Store timezone-aware timestamps as UTC. Use source revisions when available;
  for Yahoo chart snapshots, assign local revisions atomically and document the
  ordering limitation. Preserve history and reject conflicts for explicit versions.
- Never commit secrets, local environment files, SQLite databases, virtual
  environments, generated output, or dependency directories.
- Update README.md and docs/design.md when behavior or setup changes.
- Run `backend/.venv/bin/python -m pytest backend/tests -q` from the repository root.
- Run `npm test` and `npm run build` from `frontend/` after frontend changes.
- Keep synthetic market fixtures exclusively in tests; never ship demo prices.
- Preserve TradingView chart attribution. Keep WebSocket and HTTP access same-origin.
- Report validation results and checks that could not be run.

## Current authorized milestone

Prompt 08 connects persistent Yahoo WebSocket observations to the existing candle
calculator and Vue timeframe selector, while preserving the polling fallback.
Document actual results in docs/verification.md and docs/design-pipeline.md.
Prompt 09 is authorized: read-only arrival-order SQLite replay, Vue controls, tests and evidence.
Do not create report.md; the project owner writes it.
