"""Prompt 08: one persistent upstream, committed raw arrivals, derived chart states."""
import asyncio
import json
import logging
from datetime import datetime, timezone

from backend.app.services.exchange_calendar import regular_session
from backend.app.services.minute_candles import NEW_YORK, parse_time
from backend.app.services.yahoo_stream import normalize_quote

log = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


class YahooQuoteConnection:
    """Pinned yfinance 0.2.66 subscription/decoder with explicit connection ownership.

    We own recv/reconnect rather than the library's unbounded listen loop (which
    loops on clean close). The two private members are isolated here and tested
    against the pinned release. No alternate provider or invented quotes.
    """
    def __init__(self):
        from yfinance import AsyncWebSocket
        self.client = AsyncWebSocket(verbose=False)

    async def open(self):
        await self.client.__aenter__()
        await self.client.subscribe(["AAPL"])

    async def receive(self):
        frame = await self.client._ws.recv()
        envelope = json.loads(frame)
        return self.client._decode_message(envelope.get("message", ""))

    async def close(self):
        await self.client.close()


class LiveCandleFeed:
    def __init__(self, raw_repository, history, polling, *, connection_factory=YahooQuoteConnection,
                 enabled=True, clock=utc_now, retry_seconds=2, idle_seconds=60):
        self.repository, self.history, self.polling = raw_repository, history, polling
        self.connection_factory, self.enabled, self.clock = connection_factory, enabled, clock
        self.retry_seconds, self.idle_seconds = retry_seconds, idle_seconds
        self.state = "starting" if enabled else "disabled"
        self.connected = False
        self.failures = 0
        self.error = None
        self.latest = None
        self.last_arrival = None
        self.last_receipt = None
        self.ignored = 0
        self.sequence = 0
        self.clients = set()
        self.lock = asyncio.Lock()
        self.connection = None

    def signal(self):
        self.sequence += 1
        for queue in list(self.clients):
            if queue.empty():
                queue.put_nowait(True)  # Coalesced notification; consumers get authoritative full state.

    def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        self.clients.add(queue)
        return queue

    async def restore(self):
        rows = await asyncio.to_thread(self.repository.arrivals)
        rows = [row for row in rows if row["symbol"] == "AAPL" and row["source"] == "yahoo_finance_websocket"]
        if rows:
            latest = max(rows, key=lambda row: row["source_time_ms"])
            self.latest = {"source_time_utc": latest["source_time_utc"],
                           "backend_receipt_time_utc": latest["receipt_time_utc"]}
            self.last_receipt = parse_time(rows[-1]["receipt_time_utc"])

    async def accept(self, message, *, correction_of=None, correction_evidence=None):
        receipt = self.clock()
        try:
            quote = normalize_quote(message, "AAPL", receipt)
        except (ValueError, TypeError, OverflowError, OSError):
            self.ignored += 1
            return None
        async with self.lock:
            # Short serialized transaction completes before state publication. No
            # cancellable worker-thread write can outlive receiver shutdown.
            recorded = self.repository.record(message, receipt, correction_of=correction_of,
                                              correction_evidence=correction_evidence)
            if self.latest is None or parse_time(quote["source_time_utc"]) >= parse_time(self.latest["source_time_utc"]):
                self.latest = quote
            self.last_receipt = receipt
            self.last_arrival = {"arrival_sequence": recorded.arrival_sequence,
                                 "observation_id": recorded.observation_id,
                                 "disposition": recorded.disposition, "revision": recorded.revision,
                                 "source_time_utc": quote["source_time_utc"],
                                 "receipt_time_utc": receipt.isoformat(), "price": quote["price"]}
            self.state, self.failures, self.error = "available", 0, None
            self.signal()  # record() has committed before this notification.
        return recorded

    def preferred_dataset(self):
        fresh = self.latest is not None and (self.clock() - parse_time(self.latest["source_time_utc"])).total_seconds() <= 180
        return "raw" if self.connected and self.state == "available" and fresh else "polling"

    def status(self, dataset):
        now = self.clock()
        poll = self.polling.status()
        session = regular_session(now.astimezone(NEW_YORK).date())
        market = "open" if session and session.open <= now < session.close else "closed"
        if dataset == "raw":
            event = self.latest["source_time_utc"] if self.latest else None
            receipt = self.latest["backend_receipt_time_utc"] if self.latest else None
            source, transport, label = "yahoo_finance_websocket", "upstream_websocket", "Streamed observations · delay not guaranteed"
            state = self.state
        else:
            event, receipt = poll["latest_event_time"], poll["latest_receipt_time"]
            source, transport, label = "yahoo_finance_chart", "http_polling", "Polling fallback · delayed/polled"
            state = poll["state"]
        age = max(0, (now - parse_time(event)).total_seconds()) if event else None
        return {"symbol": "AAPL", "source": source, "transport": transport, "state": state,
                "upstream_state": self.state, "upstream_connected": self.connected,
                "upstream_error": self.error, "upstream_failures": self.failures,
                "fallback_active": dataset == "polling", "polling_state": poll["state"],
                "data_label": label, "is_realtime": False, "market_status": market,
                "latest_event_time": event, "latest_receipt_time": receipt,
                "latest_upstream_source_time": self.latest["source_time_utc"] if self.latest else None,
                "last_upstream_receipt_time": self.last_receipt.isoformat() if self.last_receipt else None,
                "last_successful_poll": poll["last_successful_poll"],
                "data_age_seconds": age, "stale": age is None or age > 180,
                "age_basis": "source_observation_time" if dataset == "raw" else "polled_candle_start",
                "ignored_messages": self.ignored, "provider_delay_seconds": None}

    async def snapshot(self, timeframe="1m"):
        async with self.lock:
            now, sequence = self.clock(), self.sequence
            dataset = self.preferred_dataset()
            result = await asyncio.to_thread(self.history.calculate, timeframe=timeframe, dataset=dataset, as_of=now)
            if not result["candles"]:
                alternate = "polling" if dataset == "raw" else "raw"
                other = await asyncio.to_thread(self.history.calculate, timeframe=timeframe, dataset=alternate, as_of=now)
                if other["candles"]:
                    result, dataset = other, alternate
            result["candles"] = result["candles"][-2000:]
            stamps = {row["timestamp"] for row in result["candles"]}
            result["metadata"] = [row for row in result["metadata"] if row["timestamp"] in stamps]
            return dict(result, type="snapshot", sequence=sequence, feed_status=self.status(dataset),
                        last_recorded_arrival=self.last_arrival)

    async def run(self):
        if not self.enabled:
            return
        try:
            while True:
                self.state, self.error = "connecting", None
                self.signal()
                connection = None
                try:
                    connection = self.connection_factory()
                    self.connection = connection
                    await asyncio.wait_for(connection.open(), timeout=15)
                    self.connected, self.state = True, "waiting"
                    self.signal()
                    # Application owns the reconnect deadline and resubscribes on
                    # each new connection, including clean remote closes.
                    while True:
                        message = await asyncio.wait_for(connection.receive(), timeout=self.idle_seconds)
                        await self.accept(message)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.connected, self.state = False, "unavailable"
                    self.failures += 1
                    self.error = "Yahoo subscription or persistence unavailable; reconnecting"
                    log.warning("Upstream subscription failed; retrying", exc_info=True)
                    self.signal()
                finally:
                    self.connected = False
                    try:
                        if connection is not None:
                            await asyncio.wait_for(connection.close(), timeout=5)
                    except Exception:
                        log.warning("Upstream close failed", exc_info=True)
                    self.connection = None
                await asyncio.sleep(min(30, self.retry_seconds * 2 ** min(max(self.failures - 1, 0), 4)))
        finally:
            self.connected, self.state = False, "stopped"
            self.signal()
