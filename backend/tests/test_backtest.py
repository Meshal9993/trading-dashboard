"""Backtest integration checks against constructed arrival journals."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.db.database import Database
from backend.app.db.raw_observations import RawObservationRepository
from backend.app.main import create_app
from backend.app.schemas.backtest import BacktestRequest
from backend.app.services.backtest import run_backtest
from backend.app.services.sma import with_sma
from backend.tests.evidence.backtest_server import START, TAPE, seed

REQUEST = {
    "symbol": "AAPL", "start_date": "2026-11-09", "end_date": "2026-11-09",
    "initial_cash": "1000.00", "short_period": 2, "long_period": 3,
}
M = 1_000_000


def add(repo, minute, second, price, *, receipt=None, correction_of=None):
    source = START + timedelta(minutes=minute, seconds=second)
    received = source + timedelta(seconds=1) if receipt is None else START + timedelta(seconds=receipt)
    return repo.record({"id": "AAPL", "price": price, "time": int(source.timestamp() * 1000),
                        "exchange": "NMS", "fixture": "Constructed backtest test"},
                       received, correction_of=correction_of,
                       correction_evidence="Constructed correction" if correction_of else None)


def repository(path):
    db = Database(path)
    db.initialize()
    return RawObservationRepository(db)


def calculate(repo, **overrides):
    config = BacktestRequest.model_validate({**REQUEST, **overrides})
    return run_backtest(config, repo.arrivals())


def directions(result):
    return [(s["side"], s["short"], s["long"]) for s in result["signals"]]


def test_hand_calculation_batch_and_every_incremental_prefix(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    seed(path)
    repo = RawObservationRepository(Database(path))
    arrivals = repo.arrivals()
    config = BacktestRequest.model_validate(REQUEST)
    full = run_backtest(config, arrivals)
    assert full["completed_candles"] == 7
    assert directions(full) == [("up", 16, 13), ("down", 4, 9)]
    assert [f["price_micros"] / M for f in full["fills"]] == [17, 5]
    assert [f["arrival_sequence"] for f in full["fills"]] == [6, 9]
    assert [(o["side"], o["quantity"], o["status"]) for o in full["orders"]] == [
        ("buy", 1, "filled"), ("close", 1, "filled"),
    ]
    assert full["performance"] == {
        "cash_micros": 988 * M, "shares": 0, "average_cost_micros": None,
        "realized_micros": -12 * M, "unrealized_micros": 0, "total_micros": -12 * M,
        "equity_micros": 988 * M, "price_micros": 5 * M,
        "price_time": "2026-11-09T15:07:04+00:00",
    }
    assert [p["equity_micros"] / M for p in full["equity_curve"]] == [1000] * 6 + [990, 984, 988]

    closes = [13, 10, 7, 13, 19, 7, 1]
    candles = [{"timestamp": str(i), "close": close} for i, close in enumerate(closes)]
    snapshot = {"candles": candles, "metadata": [
        {"timestamp": c["timestamp"], "is_complete": True} for c in candles
    ]}
    batch = with_sma(snapshot, (2, 3))["sma"]["points"]
    for size in range(len(arrivals) + 1):
        prefix = run_backtest(config, arrivals[:size])
        expected = [(p["crossover"], p["short"], p["long"])
                    for p in batch[:prefix["completed_candles"]] if p["crossover"]]
        assert directions(prefix) == expected
        assert all(f["arrival_sequence"] <= size for f in prefix["fills"])
        assert prefix["signals"] == full["signals"][:len(prefix["signals"])]
    assert run_backtest(config, arrivals[:5])["orders"] == []  # Signal candle still forming.


def test_repeated_endpoint_runs_do_not_modify_the_paper_account(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    seed(path)
    app = create_app(path, feed_enabled=False, stream_enabled=False)
    with TestClient(app) as client:
        order = {"request_id": str(uuid4()), "side": "buy", "quantity": 2}
        client.post("/paper/accounts/live/orders", json=order).raise_for_status()
        state = client.get("/paper/accounts/live").json()
        with app.state.database.connect() as connection:
            before = connection.execute("SELECT total_changes()").fetchone()[0]
            assert before == 0
            original = {table: connection.execute(f"SELECT * FROM {table}").fetchall()
                        for table in ("paper_accounts", "paper_orders", "paper_fills", "paper_pnl_history", "observation_arrivals")}
        a = client.post("/backtest/run", json=REQUEST)
        b = client.post("/backtest/run", json=REQUEST)
        assert a.status_code == b.status_code == 200
        assert a.content == b.content
        assert client.get("/paper/accounts/live").json() == state
        with app.state.database.connect() as connection:
            for table, before_rows in original.items():
                assert connection.execute(f"SELECT * FROM {table}").fetchall() == before_rows


def test_gap_never_invents_a_candle_or_resets_the_sma(tmp_path):
    repo = repository(tmp_path / "fixture.sqlite3")
    for minute, second, price in TAPE:
        if minute != 3:  # Skip the tie candle entirely.
            add(repo, minute, second, price)
    result = calculate(repo)
    assert result["completed_candles"] == 6
    assert directions(result) == [("up", 13, 12), ("down", 4, 9)]
    assert result["performance"]["equity_micros"] == 988 * M


def test_only_pre_deadline_corrections_can_change_a_signal(tmp_path):
    for late in (False, True):
        repo = repository(tmp_path / f"revision-{late}.sqlite3")
        for minute, second, price in TAPE[:4]:
            add(repo, minute, second, price)
        target = add(repo, 4, 50, 19)
        add(repo, 4, 50, 1, receipt=301 if late else 299, correction_of=target.observation_id)
        for minute, second, price in TAPE[5:]:
            add(repo, minute, second, price)
        result = calculate(repo)
        if late:
            assert directions(result) == [("up", 16, 13), ("down", 4, 9)]
            assert [f["price_micros"] for f in result["fills"]] == [17 * M, 5 * M]
        else:
            assert directions(result) == [("up", 4, 3)]
            assert [f["price_micros"] for f in result["fills"]] == [5 * M]


def test_late_and_same_boundary_quotes_cannot_fill_and_last_order_stays_pending(tmp_path):
    repo = repository(tmp_path / "fixture.sqlite3")
    for minute, second, price in TAPE[:5]:
        add(repo, minute, second, price)
    add(repo, 4, 59, 999, receipt=301)  # Arrived after the signal and belongs to the old candle.
    add(repo, 5, 1, 17)  # First eligible price.
    add(repo, 5, 50, 7)
    add(repo, 6, 50, 1)
    add(repo, 7, 0, 5, receipt=420)  # Receipt exactly at the exit decision boundary.
    add(repo, 7, 0, 5, receipt=421)  # An exact duplicate is not a fresh quote.
    result = calculate(repo)
    assert directions(result) == [("up", 16, 13), ("down", 4, 9)]
    assert len(result["fills"]) == 1
    assert result["fills"][0]["price_micros"] == 17 * M
    assert result["orders"][-1]["status"] == "pending"
    assert result["performance"]["shares"] == 1
    assert result["performance"]["cash_micros"] == 983 * M
    assert result["performance"]["unrealized_micros"] == -16 * M
    assert result["performance"]["equity_micros"] == 984 * M


def test_funds_rejection_and_a_date_without_saved_data(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    seed(path)
    repo = RawObservationRepository(Database(path))
    result = calculate(repo, initial_cash="10.00")
    assert result["fills"] == []
    assert len(result["orders"]) == 1
    assert result["orders"][0]["rejection_reason"] == "Insufficient cash at observed price"
    assert result["performance"]["equity_micros"] == 10 * M
    empty = calculate(repo, start_date="2026-11-10", end_date="2026-11-10")
    assert (empty["completed_candles"], empty["as_of"], empty["equity_curve"]) == (0, None, [])
    assert not empty["orders"]
    assert empty["warnings"]


@pytest.mark.parametrize("override", [
    {"symbol": "MSFT"}, {"initial_cash": "0"}, {"initial_cash": "1.0000001"},
    {"short_period": 3}, {"short_period": 1.5}, {"start_date": "2026-11-10"},
])
def test_endpoint_rejects_invalid_configuration(tmp_path, override):
    with TestClient(create_app(tmp_path / "fixture.sqlite3", feed_enabled=False, stream_enabled=False)) as client:
        assert client.post("/backtest/run", json={**REQUEST, **override}).status_code == 422
