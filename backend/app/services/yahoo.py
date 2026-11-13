"""Unofficial, free Yahoo chart adapter: polled one-minute candles, not tick streaming."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List

import httpx

from backend.app.schemas.observation import Observation

SOURCE = "yahoo_finance_chart"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL"


class FeedUnavailable(ValueError):
    pass


@dataclass
class ChartBatch:
    observations: List[Observation]
    metadata: dict
    receipt_time: datetime
    skipped_rows: int = 0


def parse_chart(payload: dict, receipt_time: datetime) -> ChartBatch:
    try:
        chart = payload["chart"]
        if chart.get("error") or not chart.get("result"):
            raise FeedUnavailable("Yahoo returned no chart data")
        result = chart["result"][0]
        metadata = result["meta"]
        if metadata.get("symbol") != "AAPL" or metadata.get("dataGranularity") != "1m":
            raise FeedUnavailable("Unexpected Yahoo symbol or interval")
        timestamps = result.get("timestamp") or []
        quotes = result["indicators"]["quote"][0]
        if any(len(quotes[key]) != len(timestamps) for key in ("open", "high", "low", "close", "volume")):
            raise FeedUnavailable("Misaligned Yahoo candle arrays")
        observations = []
        skipped = 0
        seen = set()
        for index, stamp in enumerate(timestamps):
            try:
                if isinstance(stamp, bool) or not isinstance(stamp, int):
                    raise ValueError("Invalid source timestamp")
                event_time = datetime.fromtimestamp(stamp, timezone.utc)
                if event_time > receipt_time:
                    raise ValueError("Future source timestamp")
                item = Observation(
                    symbol="AAPL", source=SOURCE, observation_id=f"AAPL:1m:{stamp}",
                    event_time=event_time, receipt_time=receipt_time,
                    **{key: quotes[key][index] for key in ("open", "high", "low", "close", "volume")},
                    revision=0,
                    # Inferred interval closure, not an assertion of provider finality.
                    is_complete=receipt_time >= event_time + timedelta(minutes=1),
                )
                if stamp in seen:
                    raise FeedUnavailable("Duplicate timestamps in Yahoo response")
                seen.add(stamp)
                observations.append(item)
            except FeedUnavailable:
                raise
            except (ValueError, TypeError, OverflowError, OSError):
                skipped += 1
        if not observations:
            raise FeedUnavailable("No valid OHLCV candles available")
        return ChartBatch(sorted(observations, key=lambda item: item.event_time), metadata, receipt_time, skipped)
    except FeedUnavailable:
        raise
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise FeedUnavailable("Malformed Yahoo chart response") from error


class YahooChartClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def fetch(self) -> ChartBatch:
        response = await self.client.get(
            URL, params={"interval": "1m", "range": "5d", "includePrePost": "false"},
            headers={"User-Agent": "IST402-TradingDashboard/0.1"}, timeout=15,
        )
        response.raise_for_status()
        received = datetime.now(timezone.utc)
        try:
            payload = response.json()
        except ValueError as error:
            raise FeedUnavailable("Yahoo returned invalid JSON") from error
        return parse_chart(payload, received)
