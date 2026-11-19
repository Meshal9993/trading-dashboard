"""Prompt 09D using constructed prices and an isolated SQLite paper account."""
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.db.raw_observations import RawObservationRepository
from backend.tests.test_paper import LIVE, START, Session, account, message

M = 1_000_000
EXPECTED = [
    (600, 4, 0, 0, 0),
    (600, 4, 0, 40, 40),
    (710, 3, 10, 30, 40),
    (710, 3, 10, -15, -5),
    (1025, 0, 25, 0, 25),
]


def history(state):
    return list(reversed(state["pnl"]["history"]))


def measurements(rows):
    return [(r["cash_micros"] / M, r["shares"], r["realized_micros"] / M,
             r["unrealized_micros"] / M, r["total_micros"] / M) for r in rows]


def mark(session, seconds):
    session.now = START + timedelta(seconds=seconds)
    session.app.state.paper.mark_live()


def test_exact_instructor_example_with_persistent_fill_and_candle_events(tmp_path):
    path = tmp_path / "paper.sqlite3"
    setup = Session(path)
    with TestClient(setup.app) as client:
        buy = setup.submit(client, "buy", 4)
        setup.quote(client, 100)
        assert measurements(history(account(client))) == EXPECTED[:1]

        setup.quote(client, 110)
        mark(setup, 59)
        assert measurements(history(account(client))) == EXPECTED[:1]
        mark(setup, 60)
        mark(setup, 60)
        assert measurements(history(account(client))) == EXPECTED[:2]

        setup.submit(client, "sell", 1)
        setup.quote(client, 110)
        assert measurements(history(account(client))) == EXPECTED[:3]
        assert account(client)["pnl"]["current"]["average_cost_micros"] == 100 * M

        setup.quote(client, 95)
        mark(setup, 120)
        mark(setup, 120)
        assert measurements(history(account(client))) == EXPECTED[:4]

        setup.submit(client, "close")
        setup.quote(client, 105)
        mark(setup, 180)
        before = account(client)
        assert measurements(history(before)) == EXPECTED
        assert [r["event"] for r in history(before)] == ["fill", "completed_candle", "fill", "completed_candle", "fill"]
        assert [r["event_key"] for r in history(before)] == [
            "fill:1", "candle:2026-11-09T15:00:00Z", "fill:2", "candle:2026-11-09T15:01:00Z", "fill:3",
        ]
        with setup.app.state.database.connect() as connection:
            rows = connection.execute("SELECT * FROM paper_pnl_history ORDER BY id").fetchall()
            assert len(rows) == 5
            assert all(r["account_id"] == "live" and r["symbol"] == "AAPL" and r["price_source"] for r in rows)
            assert [r["price_micros"] // M for r in rows] == [100, 110, 110, 95, 105]

    restart = Session(path)
    restart.now = setup.now
    with TestClient(restart.app) as client:
        assert client.post(LIVE + "/orders", json=buy).json() == before
        assert account(client) == before


def test_fractional_weighted_cost_and_legacy_fill_backfill(tmp_path):
    path = tmp_path / "paper.sqlite3"
    setup = Session(path)
    with TestClient(setup.app) as client:
        setup.submit(client, "buy", 2)
        setup.quote(client, 100)
        setup.submit(client, "buy", 1)
        setup.quote(client, 110)
        setup.submit(client, "sell", 1)
        setup.quote(client, 120)
        state = account(client)
        assert state["pnl"]["current"]["average_cost_micros"] == 103_333_333
        assert state["pnl"]["current"]["realized_micros"] == 16_666_667
        # Recreate the pre-09D state while leaving 09C's balances and fills intact.
        with setup.app.state.database.connect() as connection:
            basis = connection.execute("SELECT cost_basis_exact FROM paper_pnl_history ORDER BY id DESC LIMIT 1").fetchone()[0]
            assert basis == "620000000/3"
            connection.execute("DROP TABLE paper_pnl_history")

    restart = Session(path)
    restart.now = setup.now
    with TestClient(restart.app) as client:
        restored = account(client)
        assert restored["pnl"] == state["pnl"]
        assert restored["orders"] == state["orders"]
        setup = restart
        setup.submit(client, "close")
        setup.quote(client, 105)
        assert account(client)["pnl"]["current"]["realized_micros"] == 20 * M


def test_stale_position_is_unavailable_without_rewriting_history(tmp_path):
    setup = Session(tmp_path / "paper.sqlite3")
    with TestClient(setup.app) as client:
        setup.submit(client, "buy", 4)
        setup.quote(client, 100)
        mark(setup, 60)
        before = history(account(client))
        setup.now += timedelta(seconds=181)
        result = account(client)["pnl"]
        assert result["current"]["valuation_status"] == "unavailable"
        assert result["current"]["realized_micros"] == 0
        assert result["current"]["unrealized_micros"] is None
        assert result["current"]["total_micros"] is None
        assert history(account(client)) == before


def test_paper_replay_prices_only_the_revealed_prefix(tmp_path):
    setup = Session(tmp_path / "paper.sqlite3")
    with TestClient(setup.app) as client:
        repository = RawObservationRepository(setup.app.state.database)
        for second, price in [(2, 100), (40, 110), (62, 120)]:
            when = START + timedelta(seconds=second)
            repository.record(message(price, when), when)
        replay_id = str(uuid4())
        replay_url = f"/paper/replay/{replay_id}/frame"
        endpoint = f"/paper/accounts/{replay_id}"
        params = {"start": START.isoformat(), "end": (START + timedelta(minutes=2)).isoformat()}
        response = client.post(replay_url, params=params)
        response.raise_for_status()
        params.update(high_water=response.json()["replay"]["high_water"], advance="true")
        setup.submit(client, "buy", 4, endpoint=endpoint)
        for cursor in [0, 1, 2]:
            client.post(replay_url, params={**params, "cursor": cursor}).raise_for_status()
            rows = history(account(client, endpoint))
            if cursor < 2:
                assert measurements(rows) == EXPECTED[:1]
        assert measurements(rows) == EXPECTED[:2]
        assert rows[-1]["price_micros"] == 110 * M
        assert history(account(client)) == []
        client.post(replay_url, params={**params, "cursor": 2}).raise_for_status()
        assert history(account(client, endpoint)) == rows
