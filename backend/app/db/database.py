"""SQLite persistence; each operation owns and closes its connection."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from backend.app.db.raw_observations import RAW_SCHEMA

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_observations (
    symbol TEXT NOT NULL CHECK(length(trim(symbol)) > 0),
    source TEXT NOT NULL CHECK(length(trim(source)) > 0),
    observation_id TEXT NOT NULL CHECK(length(trim(observation_id)) > 0),
    event_time TEXT NOT NULL,
    receipt_time TEXT NOT NULL,
    open REAL NOT NULL CHECK(open >= 0),
    high REAL NOT NULL CHECK(high >= 0),
    low REAL NOT NULL CHECK(low >= 0),
    close REAL NOT NULL CHECK(close >= 0),
    volume INTEGER NOT NULL CHECK(typeof(volume) = 'integer' AND volume >= 0),
    revision INTEGER NOT NULL CHECK(typeof(revision) = 'integer' AND revision >= 0),
    is_complete INTEGER NOT NULL CHECK(is_complete IN (0, 1)),
    PRIMARY KEY (symbol, source, observation_id, revision),
    CHECK(low <= open AND low <= close AND high >= open AND high >= close)
);
CREATE INDEX IF NOT EXISTS idx_observations_event
    ON market_observations(symbol, source, event_time);
CREATE VIEW IF NOT EXISTS current_market_observations AS
SELECT o.* FROM market_observations AS o
WHERE o.revision = (
    SELECT MAX(r.revision) FROM market_observations AS r
    WHERE r.symbol = o.symbol AND r.source = o.source
      AND r.observation_id = o.observation_id
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA + RAW_SCHEMA)

    def check_health(self):
        with self.connect() as connection:
            connection.execute("SELECT 1 FROM market_observations LIMIT 1").fetchone()
