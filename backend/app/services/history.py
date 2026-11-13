"""Prompt 07A: derive all history intervals from an available one-minute snapshot."""
import math
from datetime import datetime, timedelta, timezone

from backend.app.services.exchange_calendar import regular_session
from backend.app.services.minute_candles import NEW_YORK, MinuteCandleCalculator, form_one_minute_candles, aware_utc, parse_time, stamp

TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": None}
FIELDS = {"timestamp", "open", "high", "low", "close", "volume"}


def aggregate_minutes(base, *, timeframe="1m", as_of=None):
    if timeframe not in TIMEFRAMES:
        raise ValueError("Unsupported timeframe; choose 1m, 5m, 15m, 1h or 1d")
    if base["symbol"] != "AAPL":
        raise ValueError("Only AAPL/Nasdaq regular equity sessions are configured")
    now = aware_utc(as_of if as_of is not None else parse_time(base["as_of"]))
    if parse_time(base["as_of"]) > now:
        raise ValueError("A future base snapshot cannot be used at an earlier displayed time; recalculate its one-minute history")
    statuses = {parse_time(item["timestamp"]): item for item in base.get("metadata", [])}
    groups, seen, excluded = {}, set(), 0
    for candle in sorted(base["candles"], key=lambda item: parse_time(item["timestamp"])):
        if set(candle) != FIELDS:
            raise ValueError("Each base candle must contain exactly the six candle fields")
        minute = parse_time(candle["timestamp"])
        if minute.second or minute.microsecond:
            raise ValueError("Base candles must start on a UTC minute boundary")
        if minute in seen:
            raise ValueError("Resolve revised one-minute versions before aggregation; duplicate base timestamps are not additive")
        seen.add(minute)
        state = statuses.get(minute, {})
        if minute > now or (state.get("available_at") and parse_time(state["available_at"]) > now):
            continue
        if any(isinstance(candle[key], bool) or not isinstance(candle[key], (int, float)) or
               not math.isfinite(candle[key]) or candle[key] < 0 for key in ("open", "high", "low", "close")):
            raise ValueError("Invalid base OHLC value")
        if not candle["low"] <= min(candle["open"], candle["close"]) <= max(candle["open"], candle["close"]) <= candle["high"]:
            raise ValueError("Invalid base OHLC range")
        volume = candle["volume"]
        if volume is not None and (isinstance(volume, bool) or not isinstance(volume, int) or volume < 0):
            raise ValueError("Base volume must be a nonnegative integer or null")
        session = regular_session(minute.astimezone(NEW_YORK).date())
        if session is None or not session.open <= minute < session.close:
            excluded += 1
            continue
        size = TIMEFRAMES[timeframe]
        if size is None:
            start, end = session.open, session.close
        else:
            offset = int((minute - session.open).total_seconds() // (size * 60))
            start = session.open + timedelta(minutes=offset * size)
            end = min(start + timedelta(minutes=size), session.close)
        key = (session.label, start)
        if key not in groups:
            groups[key] = {"session": session, "start": start, "end": end, "rows": []}
        groups[key]["rows"].append((candle, state, minute))

    candles, metadata = [], []
    for group in groups.values():
        rows = group["rows"]
        start, end = group["start"], group["end"]
        values = [item[0] for item in rows]
        unknown = any(item["volume"] is None for item in values)
        candles.append({"timestamp": stamp(start), "open": values[0]["open"],
                        "high": max(item["high"] for item in values),
                        "low": min(item["low"] for item in values), "close": values[-1]["close"],
                        "volume": None if unknown else sum(item["volume"] for item in values)})
        expected = int((end - start).total_seconds() // 60)
        completed = sum(now >= minute + timedelta(minutes=1) and state.get("is_complete", True)
                        for _, state, minute in rows)
        complete = now >= end and completed == len(rows)
        coverage = len(rows) == expected and completed == expected
        reasons = sorted({reason for _, state, _ in rows for reason in state.get("volume_unknown_reasons", [])})
        if unknown and not reasons:
            reasons = ["unknown_base_minute_volume"]
        metadata.append({"timestamp": stamp(start), "interval_end": stamp(end),
                         "session_date": group["session"].label, "session_open": stamp(group["session"].open),
                         "session_close": stamp(group["session"].close),
                         "is_complete": complete, "state": "complete" if complete else "forming",
                         "completion_basis": "interval_elapsed_not_immutable",
                         "expected_minutes": expected, "available_minutes": len(rows),
                         "complete_minutes": completed, "missing_minutes": expected - len(rows),
                         "is_coverage_complete": coverage, "coverage_status": "full" if coverage else "partial",
                         "volume_status": "unknown" if unknown else "known",
                         "volume_unknown_reasons": reasons,
                         "volume_basis": "sum_of_available_minute_volumes"})
    return {"symbol": base["symbol"], "source": base["source"], "timeframe": timeframe,
            "as_of": now.isoformat(), "calendar": "NASDAQ", "session_policy": "regular",
            "excluded_nonregular_minutes": excluded, "is_guaranteed_realtime": False,
            "candles": candles, "metadata": metadata}


class HistoryService:
    def __init__(self, raw_repository, polling_repository):
        self.minute_calculator = MinuteCandleCalculator(raw_repository)
        self.polling_repository = polling_repository

    def calculate(self, *, symbol="AAPL", timeframe="1m", as_of=None, dataset="raw", arrivals=None):
        now = aware_utc(as_of if as_of is not None else datetime.now(timezone.utc))
        if symbol != "AAPL":
            raise ValueError("Only AAPL is configured")
        if dataset == "raw":
            base = (self.minute_calculator.calculate(symbol=symbol, as_of=now) if arrivals is None else
                    form_one_minute_candles(arrivals, symbol=symbol, as_of=now))
        elif dataset == "polling":
            from backend.app.services.yahoo import SOURCE
            rows = self.polling_repository.current_as_of(symbol, SOURCE, now)
            base = {"symbol": symbol, "source": SOURCE, "as_of": now.isoformat(), "candles": [], "metadata": []}
            for item in rows:
                # Yahoo may append an instantaneous quote to its 1m chart response.
                # Preserve it in SQLite but never misrepresent it as a minute bar.
                if item.event_time.second or item.event_time.microsecond:
                    continue
                timestamp = stamp(item.event_time)
                base["candles"].append({"timestamp": timestamp, "open": item.open, "high": item.high,
                                        "low": item.low, "close": item.close, "volume": item.volume})
                base["metadata"].append({"timestamp": timestamp, "is_complete": item.is_complete,
                                         "available_at": item.receipt_time.isoformat()})
        else:
            raise ValueError("Dataset must be raw or polling")
        result = aggregate_minutes(base, timeframe=timeframe, as_of=now)
        result["dataset"] = dataset
        return result
