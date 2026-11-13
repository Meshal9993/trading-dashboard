"""Synthetic quotes exist only in tests; real verification uses the upstream client."""
import asyncio
from datetime import datetime, timezone

import pytest

from backend.app.services.yahoo_stream import check_subscription, normalize_quote

NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)


def quote(**changes):
    return dict({"id": "AAPL", "price": 100.25, "time": str(int(NOW.timestamp() * 1000)),
                 "day_volume": "12345"}, **changes)


def test_normalize_preserves_units_receipt_and_cumulative_volume():
    message = quote()
    result = normalize_quote(message, "AAPL", NOW)
    assert result["raw_message"] == message
    assert result["source_time_utc"] == NOW.isoformat()
    assert result["backend_receipt_time_utc"] == NOW.isoformat()
    assert result["volume_fields"]["day_volume"]["value"] == 12345
    assert "not per-trade" in result["volume_fields"]["day_volume"]["meaning"]
    assert result["is_guaranteed_realtime"] is False


def test_missing_volume_is_not_zero_and_old_quote_is_labeled():
    message = quote()
    del message["day_volume"]
    result = normalize_quote(message, "AAPL", datetime(2026, 11, 11, tzinfo=timezone.utc))
    assert result["volume_fields"] == {}
    assert result["freshness"] == "stale_or_historical"


@pytest.mark.parametrize("changes", [{"id": "MSFT"}, {"price": float("nan")}, {"price": 0},
                                    {"time": "1794322800"}, {"time": "bad"},
                                    {"day_volume": -1}, {"day_volume": 1.5}])
def test_reject_invalid_quote(changes):
    with pytest.raises(ValueError):
        normalize_quote(quote(**changes), "AAPL", NOW)


class Client:
    def __init__(self, messages=(), fail=False):
        self.messages = messages
        self.fail = fail
        self.closed = False

    async def __aenter__(self):
        if self.fail:
            raise ConnectionError("mock connection failure")
        return self

    async def subscribe(self, symbols):
        assert symbols == ["AAPL"]

    async def listen(self, callback):
        for message in self.messages:
            await callback(message)
        await asyncio.Future()

    async def close(self):
        self.closed = True


def test_bounded_subscription_receives_valid_quote_and_cleans_up():
    client = Client([{"error": "mock bad message"}, quote(time=str(int(datetime.now(timezone.utc).timestamp() * 1000)))])
    result = asyncio.run(check_subscription(timeout=1, client_factory=lambda **_: client))
    assert result["connected"] and result["subscription_sent"] and result["receipt_verified"]
    assert result["outcome"] == "quote_received" and result["ignored_messages"] == 1
    assert client.closed


def test_connected_but_no_message_is_unverified():
    client = Client()
    result = asyncio.run(check_subscription(timeout=0.02, client_factory=lambda **_: client))
    assert result["connected"] and result["subscription_sent"]
    assert not result["receipt_verified"] and result["quote"] is None
    assert result["outcome"] == "timeout_receipt_unverified" and client.closed


def test_failed_connection_is_distinct_from_no_message():
    client = Client(fail=True)
    result = asyncio.run(check_subscription(timeout=1, client_factory=lambda **_: client))
    assert not result["connected"] and not result["subscription_sent"]
    assert not result["receipt_verified"]
    assert result["error"]["type"] == "ConnectionError" and client.closed


def test_connection_attempt_has_a_deadline():
    class SlowClient(Client):
        async def __aenter__(self):
            await asyncio.Future()
    client = SlowClient()
    result = asyncio.run(check_subscription(timeout=0.02, client_factory=lambda **_: client))
    assert not result["connected"] and not result["receipt_verified"]
    assert result["outcome"] == "timeout_receipt_unverified" and client.closed


def test_listener_is_cancelled_before_transport_close():
    class OrderedClient(Client):
        cancelled = False

        async def listen(self, callback):
            await callback(quote(time=str(int(datetime.now(timezone.utc).timestamp() * 1000))))
            try:
                await asyncio.Future()
            finally:
                self.cancelled = True

        async def close(self):
            assert self.cancelled, "Listener must be cancelled before closing its transport"
            self.closed = True
    client = OrderedClient()
    result = asyncio.run(check_subscription(timeout=1, client_factory=lambda **_: client))
    assert result["receipt_verified"] and result["cleanup_error"] is None and client.closed


@pytest.mark.parametrize("timeout", [0, float("nan"), 121])
def test_rejects_unbounded_or_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        asyncio.run(check_subscription(timeout=timeout, client_factory=lambda **_: Client()))
