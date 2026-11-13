# Prompt 3 · Serve historical prices from a CSV file

~~~text
Read README.md and AGENTS.md. Use Python's built-in csv module to read a
local candle file and serve it through GET /api/history. The base resolution
is one minute: each CSV row represents one minute for one instrument. Its UTC
timestamp marks the start of that minute. For example, the 14:30 row covers
14:30:00 up to, but excluding, 14:31:00. Use exactly these six fields.

timestamp,open,high,low,close,volume
2026-11-09T14:30:00Z,100,103,99,102,1200
2026-11-09T14:31:00Z,102,104,100,101,850
2026-11-09T14:32:00Z,101,105,101,104,1500

These are synthetic one-minute candles. Use prompts/data/sample-candles.csv
if supplied, or create backend/data/sample-candles.csv from the lines above.
Open, high, low, and close are numeric prices. Volume is the number of
shares traded during that minute. Use these one-minute candles as the source
for larger timeframes, including daily candles.

Return a JSON array ordered by timestamp using the same six field names.
Start FastAPI and open the endpoint. Find 2026-11-09T14:31:00Z and check
all six values against the second row. Record the result in docs/verification.md.
~~~
