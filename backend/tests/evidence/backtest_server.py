"""Create a disposable raw journal for the Part 4.2 backtest browser check.

Run the normal backend afterward with both Yahoo workers disabled. This
fixture does not add HTTP routes or send market data over the network.
"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.db.database import Database
from backend.app.db.raw_observations import RawObservationRepository

START = datetime(2026, 11, 9, 15, tzinfo=timezone.utc)  # 10:00 a.m. New York
TAPE = (
    (0, 50, 13),
    (1, 50, 10),
    (2, 50, 7),
    (3, 50, 13),
    (4, 50, 19),
    (5, 3, 17),  # First fresh quote after the upward signal at 15:05 UTC.
    (5, 50, 7),
    (6, 50, 1),
    (7, 3, 5),  # First fresh quote after the downward signal at 15:07 UTC.
)


def seed(database_path: Path):
    database_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation keeps this fixture out of existing account databases.
    with database_path.open("xb"):
        pass
    database = Database(database_path)
    database.initialize()
    repository = RawObservationRepository(database)
    for minute, second, price in TAPE:
        source_time = START + timedelta(minutes=minute, seconds=second)
        receipt_time = source_time + timedelta(seconds=1)
        repository.record({
            "id": "AAPL",
            "time": int(source_time.timestamp() * 1_000),
            "price": price,
            "exchange": "NMS",
            "fixture": "Constructed Part 4.2 SMA backtest evidence",
        }, receipt_time)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Path to a new disposable SQLite file")
    args = parser.parse_args()
    seed(args.database)
    print(f"Seeded {len(TAPE)} constructed AAPL observations in {args.database}")


if __name__ == "__main__":
    main()
