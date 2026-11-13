import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.db.database import Database
from backend.app.db.observations import ObservationRepository
from backend.app.main import create_app
from backend.app.services.feed import MarketFeed
from backend.app.services.yahoo import FeedUnavailable, SOURCE, YahooChartClient, parse_chart

NOW = datetime(2026, 11, 6, 15, 1, 30, tzinfo=timezone.utc)
STAMP = int((NOW - timedelta(seconds=90)).timestamp())


def payload():
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": "AAPL", "dataGranularity": "1m", "currentTradingPeriod": {
            "regular": {"start": STAMP - 3600, "end": STAMP + 3600}}},
        "timestamp": [STAMP, STAMP + 60], "indicators": {"quote": [{
            "open": [100, 105], "high": [110, 111], "low": [90, 100],
            "close": [105, 108], "volume": [1000, 2000]}]}
    }]}}


def repository(tmp_path):
    db = Database(tmp_path / "market.sqlite3")
    db.initialize()
    return ObservationRepository(db)


class Provider:
    def __init__(self):
        self.data = payload()
        self.error = None

    async def fetch(self):
        if self.error:
            raise self.error
        return parse_chart(self.data, NOW)


def test_chart_conversion_and_timestamps():
    batch = parse_chart(payload(), NOW)
    first, second = batch.observations
    assert first.event_time.timestamp() == STAMP
    assert first.receipt_time == NOW
    assert (first.open, first.high, first.low, first.close, first.volume) == (100, 110, 90, 105, 1000)
    assert first.source == SOURCE
    assert first.is_complete is True
    assert second.is_complete is False


@pytest.mark.parametrize("data", [{}, {"chart": {"error": {"code": "Not Found"}, "result": None}},
                                   {"chart": {"error": None, "result": []}}])
def test_missing_feed(data):
    with pytest.raises(FeedUnavailable):
        parse_chart(data, NOW)


@pytest.mark.parametrize("change", ["wrong_symbol", "missing_volume", "misaligned", "duplicate", "no_candles"])
def test_bad_chart_shape(change):
    data = payload()
    chart = data["chart"]["result"][0]
    if change == "wrong_symbol":
        chart["meta"]["symbol"] = "MSFT"
    elif change == "missing_volume":
        del chart["indicators"]["quote"][0]["volume"]
    elif change == "misaligned":
        chart["timestamp"].append(STAMP + 120)
    elif change == "duplicate":
        chart["timestamp"][1] = STAMP
    else:
        chart["timestamp"] = []
        for key in chart["indicators"]["quote"][0]:
            chart["indicators"]["quote"][0][key] = []
    with pytest.raises(FeedUnavailable):
        parse_chart(data, NOW)


def test_nulls_skipped_without_fabricating_volume():
    data = payload()
    data["chart"]["result"][0]["indicators"]["quote"][0]["volume"][0] = None
    batch = parse_chart(data, NOW)
    assert batch.skipped_rows == 1
    assert len(batch.observations) == 1


def test_http_adapter_uses_yahoo_chart():
    async def run():
        def handler(request):
            assert request.url.host == "query1.finance.yahoo.com"
            assert request.url.params["interval"] == "1m"
            assert request.url.params["includePrePost"] == "false"
            return httpx.Response(200, json=payload())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            batch = await YahooChartClient(client).fetch()
            assert batch.receipt_time.tzinfo is not None
            assert batch.observations[0].event_time.timestamp() == STAMP
    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_http_failures(status):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await YahooChartClient(client).fetch()
    asyncio.run(run())


def test_timeout_and_bad_json():
    async def run():
        def timeout(request):
            raise httpx.ReadTimeout("offline", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with pytest.raises(httpx.ReadTimeout):
                await YahooChartClient(client).fetch()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text="bad"))) as client:
            with pytest.raises(FeedUnavailable):
                await YahooChartClient(client).fetch()
    asyncio.run(run())


def test_persistence_revisions_replay_and_restart(tmp_path):
    repo = repository(tmp_path)
    original = parse_chart(payload(), NOW).observations
    assert len(repo.store_snapshots(original)) == 2
    assert repo.store_snapshots(parse_chart(payload(), NOW + timedelta(seconds=10)).observations) == []
    data = payload()
    data["chart"]["result"][0]["indicators"]["quote"][0]["close"][0] = 106
    changed = repo.store_snapshots(parse_chart(data, NOW).observations)
    assert len(changed) == 1 and changed[0].revision == 1
    # A fresh repository reads the persisted local version rather than resetting it.
    restarted = ObservationRepository(repo.database)
    assert restarted.store_snapshots(parse_chart(data, NOW).observations) == []
    assert restarted.current("AAPL", SOURCE)[0].revision == 1
    with repo.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM market_observations").fetchone()[0] == 3


def test_completion_transition_is_revision(tmp_path):
    repo = repository(tmp_path)
    repo.store_snapshots(parse_chart(payload(), NOW).observations)
    changes = repo.store_snapshots(parse_chart(payload(), NOW + timedelta(minutes=1)).observations)
    assert len(changes) == 1 and changes[0].is_complete and changes[0].revision == 1


def test_feed_failure_recovery_status_and_fanout(tmp_path):
    async def run():
        provider = Provider()
        feed = MarketFeed(provider, repository(tmp_path), clock=lambda: NOW)
        first, second = feed.subscribe(), feed.subscribe()
        await feed.poll_once()
        assert first.get_nowait()["type"] == second.get_nowait()["type"] == "observations"
        assert feed.status()["market_status"] == "open"
        assert feed.status()["data_age_seconds"] == 30
        assert feed.status()["is_realtime"] is False
        provider.error = ConnectionError("offline")
        await feed.poll_once()
        assert feed.state == "unavailable" and feed.retry_delay() == 60
        assert feed.latest is not None
        await feed.poll_once()
        assert feed.retry_delay() == 120
        provider.error = None
        await feed.poll_once()
        assert feed.state == "available" and feed.failures == 0 and feed.retry_delay() == 30
        feed.clock = lambda: NOW + timedelta(days=2)
        assert feed.status()["market_status"] == "closed"
        assert feed.status()["stale"] is True
        assert feed.status()["data_age_seconds"] > 86400
    asyncio.run(run())


def test_degraded_unknown_market_and_slow_consumer(tmp_path):
    async def run():
        provider = Provider()
        data = provider.data["chart"]["result"][0]
        data["meta"].pop("currentTradingPeriod")
        data["indicators"]["quote"][0]["open"][0] = None
        feed = MarketFeed(provider, repository(tmp_path), clock=lambda: NOW)
        await feed.poll_once()
        assert feed.state == "degraded" and feed.status()["skipped_rows"] == 1
        assert feed.status()["market_status"] == "unknown"
        queue = feed.subscribe()
        for _ in range(65):
            feed.publish({"type": "status"})
        assert queue.get_nowait() is None
        assert queue not in feed.clients
    asyncio.run(run())


def test_rest_and_websocket_snapshot_reconnect(tmp_path):
    app = create_app(tmp_path / "market.sqlite3", feed_enabled=False)
    with TestClient(app) as client:
        app.state.repository.store_snapshots(parse_chart(payload(), NOW).observations)
        assert client.get("/feed/status").json()["state"] == "disabled"
        assert len(client.get("/observations").json()["observations"]) == 2
        for _ in range(2):
            with client.websocket_connect("/ws/market") as socket:
                snapshot = socket.receive_json()
                assert snapshot["type"] == "snapshot"
                assert len(snapshot["observations"]) == 2
                assert snapshot["status"]["is_realtime"] is False
        assert not app.state.feed.clients


def test_lifespan_worker_streams_to_clients(tmp_path):
    app = create_app(tmp_path / "market.sqlite3", provider=Provider(), feed_enabled=True, poll_seconds=1)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market") as socket:
            messages = [socket.receive_json()]
            while not any(message["type"] == "status" for message in messages):
                messages.append(socket.receive_json())
            assert messages[0]["type"] == "snapshot"
            assert messages[-1]["status"]["state"] == "available"
            assert len(client.get("/observations").json()["observations"]) == 2
        task_feed = app.state.feed
    assert not task_feed.clients


def test_five_session_bootstrap_does_not_overflow_subscriber(tmp_path):
    async def run():
        provider = Provider()
        chart = provider.data["chart"]["result"][0]
        count = 1950
        chart["timestamp"] = [STAMP - 60 * offset for offset in range(count)]
        chart["indicators"]["quote"][0] = {
            "open": [100] * count, "high": [110] * count, "low": [90] * count,
            "close": [105] * count, "volume": [1000] * count,
        }
        feed = MarketFeed(provider, repository(tmp_path), clock=lambda: NOW)
        queue = feed.subscribe()
        await feed.poll_once()
        received = []
        while not queue.empty():
            received.append(queue.get_nowait())
        assert all(message is not None for message in received)
        assert sum(len(message.get("observations", [])) for message in received) == count
        assert received[-1]["type"] == "status"
    asyncio.run(run())


def test_status_age_increases_without_new_data_and_restore(tmp_path):
    async def run():
        repo = repository(tmp_path)
        repo.store_snapshots(parse_chart(payload(), NOW).observations)
        feed = MarketFeed(Provider(), repo, clock=lambda: NOW)
        await feed.restore()
        assert feed.status()["data_age_seconds"] == 30
        feed.clock = lambda: NOW + timedelta(seconds=200)
        assert feed.status()["data_age_seconds"] == 230
        assert feed.status()["data_kind"] == "historical_or_stale"
        assert feed.status()["last_successful_poll"] is None
    asyncio.run(run())


def test_snapshot_batch_rolls_back_on_conflict(tmp_path):
    repo = repository(tmp_path)
    candles = parse_chart(payload(), NOW).observations
    repo.store_snapshots(candles)
    changed = candles[0].model_copy(update={"close": 106})
    wrong_time = candles[1].model_copy(update={"event_time": candles[1].event_time + timedelta(seconds=1)})
    from backend.app.db.observations import ObservationConflict
    with pytest.raises(ObservationConflict):
        repo.store_snapshots([changed, wrong_time])
    assert repo.current("AAPL", SOURCE)[0].revision == 0


def test_websocket_broadcasts_persisted_revision(tmp_path):
    provider = Provider()
    app = create_app(tmp_path / "market.sqlite3", provider=provider, feed_enabled=False)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market") as first, client.websocket_connect("/ws/market") as second:
            assert first.receive_json()["type"] == "snapshot"
            assert second.receive_json()["type"] == "snapshot"
            client.portal.call(app.state.feed.poll_once)
            for socket in (first, second):
                message = socket.receive_json()
                assert message["type"] == "observations"
                assert len(message["observations"]) == 2
                assert socket.receive_json()["type"] == "status"
            provider.data["chart"]["result"][0]["indicators"]["quote"][0]["close"][0] = 106
            client.portal.call(app.state.feed.poll_once)
            for socket in (first, second):
                message = socket.receive_json()
                assert message["observations"][0]["revision"] == 1
                assert message["observations"][0]["close"] == 106
                assert socket.receive_json()["type"] == "status"
            rows = client.get("/observations").json()["observations"]
            assert rows[0]["revision"] == 1
