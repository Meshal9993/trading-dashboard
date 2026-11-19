"""Constructed quotes exercise the production receipt and paper account paths."""
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.db.raw_observations import RawObservationRepository
from backend.app.main import create_app

START = datetime(2026, 11, 9, 15, tzinfo=timezone.utc)
LIVE = "/paper/accounts/live"


class Session:
    def __init__(self, database):
        self.now = START
        self.app = create_app(database, feed_enabled=False, stream_enabled=False, stream_clock=lambda: self.now)

    def submit(self, client, side, quantity=None, request_id=None, endpoint=LIVE):
        order = {"side": side, "request_id": str(request_id or uuid4())}
        if quantity is not None:
            order["quantity"] = quantity
        client.post(endpoint + "/orders", json=order).raise_for_status()
        return order

    def quote(self, client, price, *, event=None, **correction):
        self.now += timedelta(seconds=2)
        payload = message(price, event or self.now)
        if correction:
            async def accept():
                return await self.app.state.live_feed.accept(payload, **correction)
            return client.portal.call(accept)
        return client.portal.call(self.app.state.live_feed.accept, payload)


def message(price, time):
    return {"id": "AAPL", "time": int(time.timestamp() * 1000), "price": price,
            "day_volume": "10", "exchange": "NMS"}


def account(client, url=LIVE):
    response = client.get(url)
    response.raise_for_status()
    return response.json()


def assert_balance(state, cash, shares):
    assert (state["cash_micros"], state["shares"]) == (cash * 1_000_000, shares)


def test_instructor_example_pending_idempotency_and_three_fills_after_restart(tmp_path):
    path = tmp_path / "paper.sqlite3"
    setup = Session(path)
    with TestClient(setup.app) as client:
        assert_balance(account(client), 1000, 0)
        setup.quote(client, 90)  # Already observed at order submission.
        buy = setup.submit(client, "buy", 4)
        assert account(client)["orders"][0]["status"] == "pending"
        client.post(LIVE + "/orders", json=buy).raise_for_status()
        assert len(account(client)["orders"]) == 1
        assert_balance(account(client), 1000, 0)
        setup.quote(client, 100)
        assert_balance(account(client), 600, 4)
        assert client.post(LIVE + "/orders", json={**buy, "quantity": 3}).status_code == 409

        setup.submit(client, "sell", 1)
        assert account(client)["orders"][0]["status"] == "pending"
        setup.quote(client, 110)
        assert_balance(account(client), 710, 3)

        setup.submit(client, "close")
        assert account(client)["orders"][0]["status"] == "pending"
        setup.quote(client, 105)
        assert_balance(account(client), 1025, 0)

        setup.submit(client, "sell", 1)
        assert account(client)["orders"][0]["rejection_reason"] == "Insufficient shares"
        before = account(client)
        assert [o["status"] for o in before["orders"]] == ["rejected", "filled", "filled", "filled"]
        assert [o["price_micros"] for o in before["orders"][1:]] == [105_000_000, 110_000_000, 100_000_000]

    restart = Session(path)
    with TestClient(restart.app) as client:
        assert account(client) == before
        assert client.post(LIVE + "/orders", json=buy).json() == before
    with restart.app.state.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 3


@pytest.mark.parametrize("side,quantity", [("buy", 0), ("buy", -1), ("buy", 1.5), ("buy", True),
                                           ("sell", "1"), ("close", 1), ("buy", None)])
def test_invalid_order_quantities(tmp_path, side, quantity):
    with TestClient(Session(tmp_path / "paper.sqlite3").app) as client:
        response = client.post(LIVE + "/orders", json={"side": side, "quantity": quantity, "request_id": str(uuid4())})
        assert response.status_code == 422
        assert account(client)["orders"] == []


def test_pending_funds_and_position_are_rechecked_in_queue_order(tmp_path):
    setup = Session(tmp_path / "paper.sqlite3")
    with TestClient(setup.app) as client:
        setup.submit(client, "buy", 4)
        setup.submit(client, "buy", 4)
        setup.quote(client, 150)
        state = account(client)
        assert_balance(state, 400, 4)
        assert state["orders"][0]["rejection_reason"] == "Insufficient cash at observed price"
        setup.submit(client, "sell", 3)
        setup.submit(client, "sell", 3)
        setup.quote(client, 90)
        state = account(client)
        assert_balance(state, 670, 1)
        assert state["orders"][0]["rejection_reason"] == "Insufficient shares at execution"


def test_duplicate_revision_late_and_future_source_prices_do_not_fill(tmp_path):
    setup = Session(tmp_path / "paper.sqlite3")
    with TestClient(setup.app) as client:
        first = setup.quote(client, 99)
        event = setup.now
        setup.submit(client, "buy", 1)
        assert setup.quote(client, 99, event=event).disposition == "duplicate"
        assert setup.quote(client, 100, event=event, correction_of=first.observation_id,
                           correction_evidence="Constructed revision").disposition == "revision"
        setup.quote(client, 98, event=event - timedelta(seconds=1))
        setup.quote(client, 110, event=setup.now + timedelta(minutes=5))
        assert account(client)["orders"][0]["status"] == "pending"
        setup.quote(client, 101)
        assert_balance(account(client), 899, 1)


def test_pending_order_waits_through_restart(tmp_path):
    path = tmp_path / "paper.sqlite3"
    setup = Session(path)
    with TestClient(setup.app) as client:
        setup.submit(client, "buy", 1)
    restart = Session(path)
    with TestClient(restart.app) as client:
        assert account(client)["orders"][0]["status"] == "pending"
        restart.quote(client, 100)
        assert_balance(account(client), 900, 1)


def test_replay_orders_wait_for_next_revealed_quote_and_keep_live_account_separate(tmp_path):
    setup = Session(tmp_path / "paper.sqlite3")
    with TestClient(setup.app) as client:
        repository = RawObservationRepository(setup.app.state.database)
        for seconds, price in [(1, 100), (2, 110), (3, 105)]:
            when = START + timedelta(seconds=seconds)
            repository.record(message(price, when), when)
        replay_id = str(uuid4())
        params = {"start": START.isoformat(), "end": (START + timedelta(minutes=1)).isoformat()}
        replay_url = f"/paper/replay/{replay_id}/frame"
        endpoint = f"/paper/accounts/{replay_id}"
        first = client.post(replay_url, params=params)
        first.raise_for_status()
        params["high_water"] = first.json()["replay"]["high_water"]
        setup.now = START + timedelta(hours=2)  # Playback happens after the recorded market session.
        setup.submit(client, "buy", 4, endpoint=endpoint)
        client.get("/replay/frame", params={**params, "advance": "true"}).raise_for_status()
        assert_balance(account(client, endpoint), 1000, 0)
        assert client.post(replay_url, params={**params, "cursor": 1, "advance": "true"}).status_code == 409
        step = {**params, "advance": "true"}
        client.post(replay_url, params=step).raise_for_status()
        client.post(replay_url, params=step).raise_for_status()
        replayed_buy = account(client, endpoint)
        assert_balance(replayed_buy, 600, 4)
        fill = replayed_buy["orders"][0]
        assert datetime.fromisoformat(fill["executed_at"]) == setup.now
        assert datetime.fromisoformat(fill["receipt_time_utc"]) == START + timedelta(seconds=1)
        setup.submit(client, "sell", 1, endpoint=endpoint)
        client.post(replay_url, params={**params, "cursor": 1, "advance": "false", "timeframe": "5m"}).raise_for_status()
        assert account(client, endpoint)["orders"][0]["status"] == "pending"
        client.post(replay_url, params={**step, "cursor": 1}).raise_for_status()
        assert_balance(account(client, endpoint), 710, 3)
        setup.submit(client, "close", endpoint=endpoint)
        client.post(replay_url, params={**step, "cursor": 2}).raise_for_status()
        assert_balance(account(client, endpoint), 1025, 0)
        assert_balance(account(client), 1000, 0)
        assert account(client)["orders"] == []

def test_failed_account_update_rolls_back_fill_and_market_arrival(tmp_path):
    setup = Session(tmp_path / "paper.sqlite3")
    with TestClient(setup.app) as client:
        setup.submit(client, "buy", 1)
        with setup.app.state.database.connect() as connection:
            connection.execute("""
                CREATE TRIGGER test_fail_balance BEFORE UPDATE OF cash_micros ON paper_accounts
                BEGIN SELECT RAISE(ABORT, 'Constructed transaction failure'); END
            """)
        with pytest.raises(sqlite3.IntegrityError):
            setup.quote(client, 100)
        assert_balance(account(client), 1000, 0)
        assert account(client)["orders"][0]["status"] == "pending"
        with setup.app.state.database.connect() as connection:
            assert connection.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM observation_arrivals").fetchone()[0] == 0


def test_restart_does_not_treat_future_source_quote_as_eligible_baseline(tmp_path):
    path = tmp_path / "paper.sqlite3"
    setup = Session(path)
    with TestClient(setup.app) as client:
        setup.submit(client, "buy", 1)
        future = setup.quote(client, 80, event=START + timedelta(seconds=30))
        assert future is not None
        assert account(client)["orders"][0]["status"] == "pending"
    restart = Session(path)
    with TestClient(restart.app) as client:
        restart.quote(client, 100)
        assert_balance(account(client), 900, 1)
