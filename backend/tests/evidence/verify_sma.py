"""Build a disposable Prompt 09A fixture and check history against replay.

Run from the repository root with PYTHONPATH=. and pass a new SQLite path.
Only the test database contains the instructor's constructed prices.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.db.database import Database
from backend.app.db.raw_observations import RawObservationRepository
from backend.app.main import create_app

CLOSES = [13, 10, 7, 13, 19, 7, 1]
EXPECTED = [
    (None, None, None),
    (11.5, None, None),
    (8.5, 10, None),
    (10, 10, None),
    (16, 13, "up"),
    (13, 13, None),
    (4, 9, "down"),
]
BASE = datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc)


def create_fixture(path: Path):
    # Exclusive creation prevents accidentally inserting test prices into existing data.
    with path.open("xb"):
        pass
    database = Database(path)
    database.initialize()
    repository = RawObservationRepository(database)
    receipts = []
    for offset, close in enumerate(CLOSES):
        source = BASE + timedelta(minutes=offset, seconds=5)
        receipt = BASE + timedelta(minutes=offset + 1)
        repository.record(
            {"id": "AAPL", "time": int(source.timestamp() * 1000), "price": close,
             "day_volume": 1000 + offset * 10, "exchange": "NMS",
             "fixture": "Constructed instructor example for Prompt 09A"},
            receipt,
        )
        receipts.append(receipt.isoformat())
    return receipts


def verify(path: Path, receipts):
    periods = {"sma_short": 2, "sma_long": 3}
    app = create_app(path, feed_enabled=False, stream_enabled=False)
    with TestClient(app) as client:
        batch = client.get("/history", params={"as_of": receipts[-1], **periods})
        batch.raise_for_status()
        candles = batch.json()["candles"]
        points = batch.json()["sma"]["points"]
        assert [candle["close"] for candle in candles] == CLOSES
        assert [(row["short"], row["long"], row["crossover"]) for row in points] == EXPECTED

        replay_query = {"start": receipts[0], "end": receipts[-1], "cursor": 0, **periods}
        replay_points = []
        for index, point in enumerate(points, start=1):
            response = client.get("/replay/frame", params=replay_query)
            response.raise_for_status()
            frame = response.json()
            assert frame["replay"]["processed"] == index
            assert frame["sma"]["points"] == points[:index]
            assert frame["sma"]["points"][-1] == point
            replay_points.append(frame["sma"]["points"][-1])
            replay_query.update(cursor=frame["replay"]["cursor"], high_water=frame["replay"]["high_water"])
        assert frame["replay"]["complete"]
    return {
        "result": "PASS",
        "fixture": "Constructed instructor closes, not Yahoo market data",
        "timeframe": "1m",
        "short_period": 2,
        "long_period": 3,
        "closes": CLOSES,
        "expected_short_long_and_crossover": EXPECTED,
        "batch_points": points,
        "incremental_replay_points": replay_points,
        "comparisons": "All seven replay prefixes matched batch, including null values and ties.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipts = create_fixture(args.database)
    result = verify(args.database, receipts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("PASS: instructor values and crossover signals matched at all seven replay steps.")


if __name__ == "__main__":
    main()
