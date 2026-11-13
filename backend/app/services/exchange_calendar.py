"""Offline Nasdaq regular-equity sessions via pandas_market_calendars.

The package's NASDAQ alias uses its shared NYSE US-equity calendar. Verified
against Nasdaq's official 2026 holiday/early-close schedule; not a live halt feed.
"""
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

import pandas_market_calendars as market_calendars


@dataclass(frozen=True)
class Session:
    label: str
    open: datetime
    close: datetime


@lru_cache(maxsize=1)
def calendar():
    return market_calendars.get_calendar("NASDAQ")


@lru_cache(maxsize=1024)
def regular_session(day):
    schedule = calendar().schedule(start_date=day, end_date=day)
    if schedule.empty:
        return None
    row = schedule.iloc[0]
    return Session(day.isoformat(), row["market_open"].to_pydatetime(), row["market_close"].to_pydatetime())
