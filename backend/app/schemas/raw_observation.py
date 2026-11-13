"""Lossless decoded-message validation for the Prompt 06 recorder."""
import json
from datetime import datetime, timedelta, timezone


def prepare_message(message, receipt_time):
    if not isinstance(message, dict):
        raise ValueError("A decoded source message must be a JSON object")
    symbol = message.get("id")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Source message must include a ticker in id")
    raw_time = message.get("time")
    if isinstance(raw_time, bool) or not isinstance(raw_time, (int, str)):
        raise ValueError("Source time must be integer epoch milliseconds")
    milliseconds = int(raw_time)
    if not 946684800000 <= milliseconds < 4102444800000:
        raise ValueError("Source time must be plausible epoch milliseconds")
    if receipt_time.tzinfo is None or receipt_time.utcoffset() is None:
        raise ValueError("Receipt time must include a timezone")
    # No price, volume or unknown source fields are discarded or coerced here.
    payload = json.dumps(message, sort_keys=True, separators=(",", ":"), allow_nan=False)
    event = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=milliseconds)
    return symbol, milliseconds, event.isoformat(timespec="microseconds"), receipt_time.astimezone(timezone.utc).isoformat(timespec="microseconds"), payload
