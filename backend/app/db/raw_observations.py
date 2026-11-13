"""Append-only raw observations and a durable, duplicate-aware receipt journal."""
import json
from dataclasses import dataclass

from backend.app.schemas.raw_observation import prepare_message

SOURCE = "yahoo_finance_websocket"

RAW_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK(length(trim(source)) > 0),
    symbol TEXT NOT NULL,
    source_time_ms INTEGER NOT NULL,
    source_time_utc TEXT NOT NULL,
    source_timestamp_units TEXT NOT NULL DEFAULT 'milliseconds_since_unix_epoch',
    first_receipt_time_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 0),
    supersedes_id INTEGER UNIQUE REFERENCES raw_observations(observation_id),
    correction_evidence TEXT,
    CHECK((supersedes_id IS NULL AND revision = 0 AND correction_evidence IS NULL)
       OR (supersedes_id IS NOT NULL AND revision > 0 AND correction_evidence IS NOT NULL AND length(trim(correction_evidence)) > 0))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_message_identity
    ON raw_observations(source, symbol, payload_json, coalesce(supersedes_id, 0));
CREATE INDEX IF NOT EXISTS idx_raw_source_time
    ON raw_observations(source, symbol, source_time_ms);
CREATE TABLE IF NOT EXISTS observation_arrivals (
    arrival_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER NOT NULL REFERENCES raw_observations(observation_id),
    receipt_time_utc TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK(disposition IN ('inserted', 'duplicate', 'revision')),
    correction_evidence TEXT
);
CREATE INDEX IF NOT EXISTS idx_arrivals_observation ON observation_arrivals(observation_id);
"""


class CorrectionConflict(ValueError):
    pass


@dataclass(frozen=True)
class RecordedArrival:
    observation_id: int
    arrival_sequence: int
    disposition: str
    revision: int


class RawObservationRepository:
    def __init__(self, database):
        self.database = database

    def record(self, message, receipt_time, *, source=SOURCE, correction_of=None, correction_evidence=None):
        symbol, stamp, event, receipt, payload = prepare_message(message, receipt_time)
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Source is required")
        if correction_of is None and correction_evidence is not None:
            raise ValueError("Correction evidence requires a correction target")
        if correction_of is not None:
            if isinstance(correction_of, bool) or not isinstance(correction_of, int) or correction_of <= 0:
                raise ValueError("Correction target must be an observation id")
            if not isinstance(correction_evidence, str) or not correction_evidence.strip():
                raise ValueError("Explicit correction evidence is required")
        with self.database.connect() as connection:
            # Serializes comparison, local revision assignment and both journal writes.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM raw_observations WHERE source=? AND symbol=? AND payload_json=? "
                "AND supersedes_id IS ?", (source, symbol, payload, correction_of)
            ).fetchone()
            if row:
                observation_id, revision, disposition = row["observation_id"], row["revision"], "duplicate"
            else:
                revision = 0
                if correction_of is not None:
                    parent = connection.execute(
                        "SELECT * FROM raw_observations WHERE observation_id=?", (correction_of,)
                    ).fetchone()
                    if not parent or (parent["source"], parent["symbol"]) != (source, symbol):
                        raise CorrectionConflict("Correction target must exist for this source and symbol")
                    if connection.execute("SELECT 1 FROM raw_observations WHERE supersedes_id=?", (correction_of,)).fetchone():
                        raise CorrectionConflict("Target already superseded; identify the correct latest version")
                    revision = parent["revision"] + 1
                cursor = connection.execute(
                    "INSERT INTO raw_observations (source,symbol,source_time_ms,source_time_utc,"
                    "first_receipt_time_utc,payload_json,revision,supersedes_id,correction_evidence) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (source, symbol, stamp, event, receipt, payload, revision, correction_of, correction_evidence)
                )
                observation_id = cursor.lastrowid
                disposition = "revision" if correction_of is not None else "inserted"
            arrival = connection.execute(
                "INSERT INTO observation_arrivals (observation_id,receipt_time_utc,disposition,correction_evidence) "
                "VALUES (?,?,?,?)", (observation_id, receipt, disposition, correction_evidence)
            )
            return RecordedArrival(observation_id, arrival.lastrowid, disposition, revision)

    def arrivals(self, *, after_sequence=0):
        """Read-only journal inspection, not Prompt 09 replay or a latest-state view."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT a.arrival_sequence,a.receipt_time_utc,a.disposition,"
                "a.correction_evidence AS arrival_correction_evidence,o.* "
                "FROM observation_arrivals a JOIN raw_observations o USING(observation_id) "
                "WHERE a.arrival_sequence>? ORDER BY a.arrival_sequence", (after_sequence,)
            ).fetchall()
        return [dict(dict(row), source_fields=json.loads(row["payload_json"])) for row in rows]
