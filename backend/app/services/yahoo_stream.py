"""Prompts 05–06: bounded quote reception and raw SQLite recording.

Run from the repository root: python -m backend.app.services.yahoo_stream --timeout 60
The CLI records raw arrivals; no candle aggregation or polling-worker changes.
"""
import argparse
import asyncio
import json
import math
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc)


def normalize_quote(message, symbol, receipt_time):
    if not isinstance(message, dict) or message.get("id") != symbol:
        raise ValueError("Not a quote for the subscribed symbol")
    price = message.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise ValueError("Missing or invalid quote price")
    raw_time = message.get("time")
    if isinstance(raw_time, bool) or not isinstance(raw_time, (int, str)):
        raise ValueError("Missing or invalid source timestamp")
    milliseconds = int(raw_time)
    # Prevent interpreting seconds as milliseconds while preserving raw units/value.
    if not 946684800000 <= milliseconds < 4102444800000:
        raise ValueError("Source timestamp is not a plausible epoch-millisecond value")
    source_time = datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
    age = (receipt_time - source_time).total_seconds()
    if age < -60:
        raise ValueError("Source timestamp is unexpectedly in the future")
    volumes = {}
    for name, meaning in (("day_volume", "reported cumulative day/session volume; not per-trade"),
                          ("last_size", "reported last-trade size; not a session total")):
        if name in message:
            raw = message[name]
            if isinstance(raw, bool) or not isinstance(raw, (int, str)):
                raise ValueError("Invalid volume field")
            value = int(raw)
            if value < 0:
                raise ValueError("Negative volume field")
            volumes[name] = {"raw_value": raw, "value": value, "meaning": meaning}
    return {
        "symbol": symbol, "price": price, "source": "yahoo_finance_websocket",
        "source_timestamp_raw": raw_time, "source_timestamp_units": "milliseconds_since_unix_epoch",
        "source_time_utc": source_time.isoformat(), "backend_receipt_time_utc": receipt_time.isoformat(),
        "source_age_seconds_at_receipt": age,
        "freshness": "stale_or_historical" if age > 180 else "recent_observation_delay_not_guaranteed",
        "is_guaranteed_realtime": False, "volume_fields": volumes,
        "raw_message": message,
    }


async def check_subscription(symbol="AAPL", timeout=60, client_factory=None, recorder=None, max_quotes=1):
    if not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Timeout must be greater than zero and at most 120 seconds")
    if isinstance(max_quotes, bool) or not isinstance(max_quotes, int) or not 1 <= max_quotes <= 1000:
        raise ValueError("Capture target must be an integer between 1 and 1000")
    if client_factory is None:
        from yfinance import AsyncWebSocket
        client_factory = AsyncWebSocket
    started = time.monotonic()
    result = {"symbol": symbol, "started_at_utc": utc_now().isoformat(),
              "timeout_seconds": timeout, "connected": False, "subscription_sent": False,
              "receipt_verified": False, "quote": None, "ignored_messages": 0,
              "outcome": "not_started", "cleanup_error": None,
              "capture_target": max_quotes, "capture_target_reached": False, "valid_quotes_received": 0,
              "recording_enabled": recorder is not None, "recorded_arrivals": [], "persistence_error": None}
    client = client_factory(verbose=False)
    listener = None
    first_quote = asyncio.get_running_loop().create_future()

    async def received(message):
        receipt = utc_now()  # Capture at handler entry, before validation/conversion.
        if first_quote.done():
            return  # Do not start extra writes after the bounded capture target.
        try:
            quote = normalize_quote(message, symbol, receipt)
        except (ValueError, TypeError, OverflowError, OSError):
            result["ignored_messages"] += 1
            return
        result["valid_quotes_received"] += 1
        result["receipt_verified"] = True
        if result["quote"] is None:
            result["quote"] = quote
        if recorder is not None:
            try:
                recorded = recorder.record(message, receipt)
                result["recorded_arrivals"].append({
                    "observation_id": recorded.observation_id, "arrival_sequence": recorded.arrival_sequence,
                    "disposition": recorded.disposition, "revision": recorded.revision,
                })
            except Exception as error:
                result["persistence_error"] = {"type": type(error).__name__, "message": str(error)}
        if result["valid_quotes_received"] >= max_quotes or result["persistence_error"]:
            result["capture_target_reached"] = result["valid_quotes_received"] >= max_quotes
            first_quote.set_result(result["quote"])

    async def subscribe_and_receive():
        nonlocal listener
        await client.__aenter__()  # yfinance opens the actual upstream connection here.
        result["connected"] = True
        result["connected_at_utc"] = utc_now().isoformat()
        await client.subscribe([symbol])
        result["subscription_sent"] = True  # Sent, not a provider acknowledgement.
        listener = asyncio.create_task(client.listen(received))
        done, _ = await asyncio.wait((listener, first_quote), return_when=asyncio.FIRST_COMPLETED)
        if first_quote in done:
            result["quote"] = first_quote.result()
            result["receipt_verified"] = True
            result["outcome"] = "persistence_failed" if result["persistence_error"] else "quote_received"
        else:
            listener.result()
            result["outcome"] = "listener_stopped_without_quote"

    try:
        await asyncio.wait_for(subscribe_and_receive(), timeout)
    except asyncio.TimeoutError:
        result["outcome"] = "timeout_target_not_reached" if result["receipt_verified"] else "timeout_receipt_unverified"
    except Exception as error:
        result["outcome"] = "connection_or_subscription_failed"
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        # Separate finite cleanup budget; yfinance may catch listener cancellation.
        if listener is not None:
            # Cancel before closing: yfinance 0.2.66's listener loops on a clean
            # socket close. Its cancellation handler closes the transport.
            listener.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(listener, return_exceptions=True), 5)
            except asyncio.TimeoutError:
                result["cleanup_error"] = "Listener cleanup exceeded five seconds"
        try:
            await asyncio.wait_for(client.close(), 5)
        except Exception as error:
            result["cleanup_error"] = type(error).__name__ + ": " + str(error)
        if not first_quote.done():
            first_quote.cancel()
    result["finished_at_utc"] = utc_now().isoformat()
    result["elapsed_seconds_including_cleanup"] = round(time.monotonic() - started, 3)
    return result


def main():
    import importlib.metadata
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL", choices=["AAPL"])
    parser.add_argument("--timeout", default=60, type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-quotes", type=int, default=1)
    default_database = Path(__file__).resolve().parents[2] / "data" / "market.sqlite3"
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("DATABASE_PATH", str(default_database))))
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 120:
        parser.error("--timeout must be greater than zero and at most 120")
    if not 1 <= args.max_quotes <= 1000:
        parser.error("--max-quotes must be between 1 and 1000")
    from backend.app.db.database import Database
    from backend.app.db.raw_observations import RawObservationRepository
    database = Database(args.database)
    database.initialize()
    result = asyncio.run(check_subscription(args.symbol, args.timeout, recorder=RawObservationRepository(database), max_quotes=args.max_quotes))
    result["database_path"] = str(database.path.resolve())
    result["environment"] = {"python": sys.version, "interpreter": sys.executable,
                             "os": platform.platform(), "yfinance": importlib.metadata.version("yfinance"),
                             "websockets": importlib.metadata.version("websockets")}
    result["command"] = [sys.executable, "-m", "backend.app.services.yahoo_stream", *sys.argv[1:]]
    rendered = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if result["capture_target_reached"] and not result["cleanup_error"] and not result["persistence_error"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
