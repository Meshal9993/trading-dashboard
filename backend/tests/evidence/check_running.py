"""Read-only running-stack checks; writes measured evidence, never predicted results."""
import argparse
import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
from websockets.asyncio.client import connect


def normalized(item):
    result = dict(item)
    for key in ("event_time", "receipt_time"):
        result[key] = datetime.fromisoformat(result[key].replace("Z", "+00:00")).isoformat(timespec="microseconds")
    result["is_complete"] = bool(result["is_complete"])
    return result


async def check(args):
    async with httpx.AsyncClient(base_url="http://127.0.0.1:5173", timeout=15) as client:
        health = (await client.get("/health")).json()
        assert health == {"status": "ok", "database": "ok"}
        snapshot = (await client.get("/observations")).json()
        assert snapshot["observations"], "No real saved observations available"
        async with connect("ws://127.0.0.1:5173/ws/market", max_size=4 * 1024 * 1024) as socket:
            wire = json.loads(await asyncio.wait_for(socket.recv(), 10))
            assert wire["type"] == "snapshot"
            assert wire["observations"] == snapshot["observations"]
            statuses = [wire["status"]]
            if args.wait_poll:
                first = wire["status"]["last_successful_poll"]
                while len(statuses) < 15:
                    message = json.loads(await asyncio.wait_for(socket.recv(), 40))
                    if message["type"] == "status":
                        statuses.append(message["status"])
                        if message["status"]["last_successful_poll"] != first:
                            break
                assert statuses[-1]["last_successful_poll"] != first, "No successful periodic poll observed"
        # A second actual WebSocket handshake proves reconnect snapshot delivery.
        async with connect("ws://127.0.0.1:5173/ws/market", max_size=4 * 1024 * 1024) as socket:
            reconnected = json.loads(await asyncio.wait_for(socket.recv(), 10))
            assert reconnected["observations"] == snapshot["observations"]
        with sqlite3.connect(args.database) as db:
            db.row_factory = sqlite3.Row
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            assert integrity == "ok"
            rows = db.execute("SELECT * FROM current_market_observations ORDER BY event_time, observation_id").fetchall()
            database_rows = [normalized(row) for row in rows[-390:]]
            assert database_rows == [normalized(row) for row in wire["observations"]]
            versions = db.execute("SELECT count(*) FROM market_observations").fetchone()[0]
        result = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "scenario": args.scenario, "result": "PASS",
            "checks": ["FastAPI health through Vite proxy", "HTTP snapshot equals actual WebSocket snapshot",
                       "WebSocket reconnect snapshot", "SQLite current rows equal WebSocket OHLCV, timestamps and revisions",
                       "SQLite integrity_check"],
            "current_database_rows": len(rows), "stored_revisions": versions,
            "websocket_candles": len(wire["observations"]), "sqlite_integrity": integrity,
            "status_samples": statuses, "observations": snapshot["observations"][-5:],
            "note": "Browser chart rendering is checked separately; this script verifies transport and storage equality."
        }
        if args.wait_poll:
            result["checks"].append("Background 30-second poll produced a newer successful-poll timestamp")
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({key: value for key, value in result.items() if key not in ("status_samples", "observations")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--wait-poll", action="store_true")
    asyncio.run(check(parser.parse_args()))
