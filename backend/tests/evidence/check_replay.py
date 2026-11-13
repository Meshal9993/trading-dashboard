"""Against the LOCAL evidence server only: replay real captured bars and check wire/storage."""
import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "docs/evidence/part4-1"


async def run():
    expected = json.loads((EVIDENCE / "live-flow.json").read_text())["observations"]
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30) as client:
        async with connect("ws://127.0.0.1:5173/ws/market", max_size=4 * 1024 * 1024) as socket:
            before = json.loads(await asyncio.wait_for(socket.recv(), 10))
            assert len(before["observations"]) == 1
            Path('/tmp/part4-1-feed-mode.json').write_text('{"mode":"replay","count":5}')
            response = await client.post('/__evidence/poll')
            response.raise_for_status()
            changed = []
            while True:
                message = json.loads(await asyncio.wait_for(socket.recv(), 10))
                if message["type"] == "status":
                    break
                assert message["type"] == "observations"
                changed.extend(message["observations"])
            assert changed == expected[1:]
            response = await client.post('/__evidence/poll')
            response.raise_for_status()
            duplicate_message = json.loads(await asyncio.wait_for(socket.recv(), 10))
            assert duplicate_message["type"] == "status", "Duplicate unexpectedly rebroadcast observations"
            after = (await client.get('/observations')).json()
            assert after["observations"] == expected
            assert after["status"]["is_realtime"] is False
            assert after["status"]["stale"] is True
    with sqlite3.connect('/tmp/part4-1-replay.sqlite3') as db:
        count = db.execute('SELECT count(*) FROM market_observations').fetchone()[0]
        assert count == 5
    result = {
        "result": "PASS", "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "scenario": "Controlled replay of five actual Yahoo candles captured in live-flow.json; not current market activity",
        "before_candles": 1, "websocket_appended_candles": len(changed), "after_candles": len(after['observations']),
        "sqlite_revision_rows_after_duplicate": count, "duplicate_result": "status only; no extra rows or observation broadcast",
        "prices_and_source_timestamps": "unchanged from real capture", "messages": changed,
        "browser_rendering": "Verified separately in before/after screenshots and DOM snapshots"
    }
    (EVIDENCE / 'controlled-replay.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


asyncio.run(run())
