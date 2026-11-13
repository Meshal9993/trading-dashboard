"""Prompt 07: one-minute candles from the saved receipt journal; no higher timeframes."""
import argparse
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.app.db.raw_observations import SOURCE

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")


def aware_utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Calculation time must include a timezone")
    return value.astimezone(UTC)


def parse_time(value):
    return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def stamp(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def counter(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        result = int(value)
    except ValueError:
        return None
    return result if result >= 0 else None


def form_one_minute_candles(arrivals, *, symbol="AAPL", as_of, source=SOURCE):
    """Read-only as-of calculation. Corrections apply at receipt, prices sort by source time.

    AAPL cumulative counters are separated by New York date. No exchange calendar
    or higher-timeframe/session-alignment engine is introduced by this function.
    """
    if symbol != "AAPL":
        raise ValueError("Prompt 07 currently supports AAPL only; other symbols need a defined session policy")
    now = aware_utc(as_of)
    active = {}
    for row in sorted(arrivals, key=lambda item: item["arrival_sequence"]):
        # An as-of snapshot is an available journal prefix. Do not skip a later
        # receipt then admit subsequent rows if the wall clock moved backward.
        if parse_time(row["receipt_time_utc"]) > now:
            break
        if row["source"] != source or row["symbol"] != symbol:
            continue
        if row["disposition"] == "duplicate":
            continue
        order = row["arrival_sequence"]
        if row["supersedes_id"] is not None:
            previous = active.pop(row["supersedes_id"], None)
            if previous is None:
                raise ValueError("Correction predecessor is not available in this as-of journal")
            order = previous[1]  # A correction retains the original tie-break position.
        active[row["observation_id"]] = (row, order)

    points = []
    for row, order in active.values():
        event = parse_time(row["source_time_utc"])
        if event > now:
            continue  # Do not use a source observation from a later displayed time.
        fields = row["source_fields"]
        price = fields.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            raise ValueError("Saved observation has an invalid price; no candle was fabricated")
        minute = event.replace(second=0, microsecond=0)
        points.append({"event": event, "minute": minute, "order": order, "price": price,
                       "counter": counter(fields.get("day_volume")),
                       "session": (event.astimezone(NEW_YORK).date(), fields.get("exchange"))})
    points.sort(key=lambda item: (item["event"], item["order"]))

    # Independent observations with the same source timestamp are not corrections.
    # Their price order has a documented tie-break; differing counters are ambiguous.
    simultaneous = defaultdict(set)
    for point in points:
        simultaneous[(point["session"], point["event"])].add(point["counter"])

    groups = {}
    previous = None
    for point in points:
        minute = point["minute"]
        if minute not in groups:
            groups[minute] = {"timestamp": stamp(minute), "open": point["price"], "high": point["price"],
                              "low": point["price"], "close": point["price"], "volume": 0,
                              "reasons": set(), "count": 0}
        group = groups[minute]
        group["high"] = max(group["high"], point["price"])
        group["low"] = min(group["low"], point["price"])
        group["close"] = point["price"]
        group["count"] += 1
        reasons = group["reasons"]
        if point["counter"] is None:
            reasons.add("missing_or_invalid_cumulative_volume")
        if previous is None or previous["session"] != point["session"]:
            reasons.add("missing_session_baseline")
        elif previous["counter"] is None:
            reasons.add("missing_cumulative_baseline")
        elif point["counter"] is not None:
            delta = point["counter"] - previous["counter"]
            if delta < 0:
                reasons.add("cumulative_counter_reset_or_regression")
            elif minute - previous["minute"] > timedelta(minutes=1):
                reasons.add("unobserved_minute_gap")
            else:
                group["volume"] += delta
        if len(simultaneous[(point["session"], point["event"])]) > 1:
            reasons.add("ambiguous_same_timestamp_volume")
        if previous is not None and len(simultaneous[(previous["session"], previous["event"])]) > 1:
            # An ambiguous boundary observation cannot be a valid next-minute baseline.
            reasons.add("ambiguous_cumulative_baseline")
        previous = point

    candles, metadata = [], []
    for minute, group in groups.items():
        candles.append({key: (None if key == "volume" and group["reasons"] else group[key])
                        for key in ("timestamp", "open", "high", "low", "close", "volume")})
        complete = now >= minute + timedelta(minutes=1)
        metadata.append({"timestamp": group["timestamp"], "is_complete": complete,
                         "state": "complete" if complete else "forming",
                         "completion_basis": "interval_elapsed_not_immutable",
                         "volume_status": "unknown" if group["reasons"] else "known",
                         "volume_unknown_reasons": sorted(group["reasons"]),
                         "observation_count": group["count"]})
    return {"symbol": symbol, "source": source, "as_of": now.isoformat(),
            "candles": candles, "metadata": metadata}


class MinuteCandleCalculator:
    def __init__(self, repository):
        self.repository = repository

    def calculate(self, *, symbol="AAPL", as_of=None):
        return form_one_minute_candles(self.repository.arrivals(), symbol=symbol,
                                      as_of=as_of if as_of is not None else datetime.now(UTC))


def main():
    from backend.app.db.database import Database
    from backend.app.db.raw_observations import RawObservationRepository
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path(__file__).resolve().parents[2] / "data" / "market.sqlite3"
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("DATABASE_PATH", str(default))))
    parser.add_argument("--symbol", choices=["AAPL"], default="AAPL")
    parser.add_argument("--as-of", type=parse_time)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("Database does not exist; record observations with the Prompt 06 receiver first")
    result = MinuteCandleCalculator(RawObservationRepository(Database(args.database))).calculate(
        symbol=args.symbol, as_of=args.as_of)
    rendered = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
