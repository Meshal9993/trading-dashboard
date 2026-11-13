"""One shared polling worker with bounded WebSocket fan-out."""
import asyncio
import logging
from datetime import datetime, timezone

from backend.app.services.yahoo import SOURCE

logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


class MarketFeed:
    def __init__(self, provider, repository, poll_seconds=30, clock=utc_now, on_change=None):
        if poll_seconds < 1:
            raise ValueError("Poll interval must be at least one second")
        self.on_change = on_change
        self.provider = provider
        self.repository = repository
        self.poll_seconds = poll_seconds
        self.clock = clock
        self.clients = set()
        self.state = "starting"
        self.metadata = {}
        self.latest = None
        self.last_success = None
        self.failures = 0
        self.error = None
        self.skipped_rows = 0

    def subscribe(self):
        queue = asyncio.Queue(maxsize=64)
        self.clients.add(queue)
        return queue

    def publish(self, message):
        if self.on_change is not None:
            self.on_change()
        for queue in list(self.clients):
            if queue.full():
                # A slow consumer must reconnect for a fresh snapshot; never silently lose candles.
                self.clients.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                queue.put_nowait(message)

    def status(self):
        now = self.clock()
        market = "unknown"
        try:
            regular = self.metadata["currentTradingPeriod"]["regular"]
            start, end = float(regular["start"]), float(regular["end"])
            if start < end:
                market = "open" if start <= now.timestamp() < end else "closed"
        except (KeyError, TypeError, ValueError):
            pass
        age = max(0, (now - self.latest.event_time).total_seconds()) if self.latest else None
        return {
            "symbol": "AAPL", "source": SOURCE, "transport": "http_polling",
            "delivery": "websocket", "state": self.state, "market_status": market,
            "data_label": "latest_available_polled_not_guaranteed_realtime",
            "is_realtime": False, "data_age_seconds": age,
            "data_kind": "historical_or_stale" if age is not None and age > 180 else ("latest_available" if age is not None else "unavailable"),
            "provider_delay_seconds": None,
            "market_status_basis": "last_yahoo_regular_session_metadata",
            "stale": age is None or age > 180,
            "latest_event_time": self.latest.event_time.isoformat() if self.latest else None,
            "latest_receipt_time": self.latest.receipt_time.isoformat() if self.latest else None,
            "last_successful_poll": self.last_success.isoformat() if self.last_success else None,
            "consecutive_failures": self.failures, "error": self.error,
            "skipped_rows": self.skipped_rows,
            "next_poll_seconds": None if self.state == "disabled" else self.retry_delay(),
            "completion_basis": "interval_elapsed_inferred_not_provider_final",
        }

    def retry_delay(self):
        return min(300, self.poll_seconds * (2 ** min(self.failures, 8)))

    async def restore(self):
        rows = await asyncio.to_thread(self.repository.current, "AAPL", SOURCE)
        self.latest = rows[-1] if rows else None

    async def poll_once(self):
        try:
            batch = await self.provider.fetch()
            changes = await asyncio.to_thread(self.repository.store_snapshots, batch.observations)
            rows = await asyncio.to_thread(self.repository.current, "AAPL", SOURCE)
            self.latest = rows[-1] if rows else self.latest
            self.metadata = batch.metadata
            self.last_success = batch.receipt_time
            self.skipped_rows = batch.skipped_rows
            self.state = "degraded" if batch.skipped_rows else "available"
            self.failures = 0
            self.error = None
            # A five-session bootstrap may contain thousands of candles. Batch the
            # delta so healthy clients are not overflowed by one successful poll.
            for offset in range(0, len(changes), 100):
                self.publish({"type": "observations", "observations": [
                    observation.model_dump(mode="json") for observation in changes[offset:offset + 100]
                ]})
        except Exception:
            # Keep cached data usable while transport/provider/database failures retry.
            logger.warning("Market feed poll failed", exc_info=True)
            self.failures += 1
            self.state = "unavailable"
            self.error = "Market data fetch or persistence failed; retrying"
        self.publish({"type": "status", "status": self.status()})

    async def run(self):
        while True:
            await self.poll_once()
            await asyncio.sleep(self.retry_delay())
