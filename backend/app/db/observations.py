"""Revision-aware storage without arrival-order assumptions."""
from typing import List, Literal

from backend.app.db.database import Database
from backend.app.schemas.observation import Observation


class ObservationConflict(ValueError):
    """A source reused an identity/revision with incompatible data."""


class ObservationRepository:
    def __init__(self, database: Database):
        self.database = database

    def store(self, observation: Observation) -> Literal["inserted", "revised", "stale", "duplicate"]:
        values = observation.model_dump(mode="json")
        # Fixed-width UTC strings sort chronologically even with fractional seconds.
        for name in ("event_time", "receipt_time"):
            values[name] = getattr(observation, name).isoformat(timespec="microseconds")
        values["is_complete"] = int(observation.is_complete)
        identity = (observation.symbol, observation.source, observation.observation_id)
        with self.database.connect() as connection:
            # Serialize read/compare/write across concurrent writers.
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM market_observations WHERE symbol=? AND source=? "
                "AND observation_id=? ORDER BY revision DESC", identity
            ).fetchall()
            if rows and rows[0]["event_time"] != values["event_time"]:
                raise ObservationConflict("An observation identity must retain its event time")
            for row in rows:
                if row["revision"] == observation.revision:
                    if any(row[key] != value for key, value in values.items() if key != "receipt_time"):
                        raise ObservationConflict("Conflicting payload for the same revision")
                    return "duplicate"
            columns = list(values)
            connection.execute(
                "INSERT INTO market_observations (" + ", ".join(columns) + ") VALUES ("
                + ", ".join("?" for _ in columns) + ")", list(values.values())
            )
            if not rows:
                return "inserted"
            return "revised" if observation.revision > rows[0]["revision"] else "stale"

    def store_snapshots(self, observations: List[Observation]) -> List[Observation]:
        """Assign local revisions atomically: Yahoo chart candles have no revision field.

        An unchanged replay keeps the first receipt; each changed snapshot appends
        a locally observed version. These are not provider sequence numbers.
        """
        changes = []
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in observations:
                row = connection.execute(
                    "SELECT * FROM market_observations WHERE symbol=? AND source=? "
                    "AND observation_id=? ORDER BY revision DESC LIMIT 1",
                    (item.symbol, item.source, item.observation_id),
                ).fetchone()
                values = item.model_dump(mode="json")
                for key in ("event_time", "receipt_time"):
                    values[key] = getattr(item, key).isoformat(timespec="microseconds")
                values["is_complete"] = int(item.is_complete)
                if row:
                    if row["event_time"] != values["event_time"]:
                        raise ObservationConflict("An observation identity must retain its event time")
                    if all(row[key] == value for key, value in values.items()
                           if key not in ("receipt_time", "revision")):
                        continue
                values["revision"] = row["revision"] + 1 if row else 0
                revised = Observation.model_validate(dict(values, is_complete=bool(values["is_complete"])))
                connection.execute(
                    "INSERT INTO market_observations (" + ", ".join(values) + ") VALUES ("
                    + ", ".join("?" for _ in values) + ")", list(values.values())
                )
                changes.append(revised)
        return changes

    def current_as_of(self, symbol, source, as_of):
        """Latest candle version actually received by the requested displayed time."""
        from datetime import timezone
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Calculation time must include a timezone")
        cutoff = as_of.astimezone(timezone.utc).isoformat(timespec="microseconds")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT o.* FROM market_observations o WHERE o.symbol=? AND o.source=? "
                "AND o.receipt_time<=? AND o.event_time<=? AND NOT EXISTS ("
                "SELECT 1 FROM market_observations r WHERE r.symbol=o.symbol AND r.source=o.source "
                "AND r.observation_id=o.observation_id AND r.revision>o.revision "
                "AND r.receipt_time<=? AND r.event_time<=?) ORDER BY o.event_time,o.observation_id",
                (symbol, source, cutoff, cutoff, cutoff, cutoff)
            ).fetchall()
        return [Observation.model_validate(dict(dict(row), is_complete=bool(row["is_complete"]))) for row in rows]

    def current(self, symbol: str, source: str) -> List[Observation]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM current_market_observations WHERE symbol=? AND source=? "
                "ORDER BY event_time, observation_id", (symbol, source)
            ).fetchall()
        result = []
        for row in rows:
            values = dict(row)
            values["is_complete"] = bool(values["is_complete"])
            result.append(Observation.model_validate(values))
        return result
