"""Verify the isolated Part 4.2 smoke account and historical tape with read-only SQL."""
import argparse
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.db.raw_observations import SOURCE
from backend.tests.evidence.backtest_server import START, TAPE

PAPER_START = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
PAPER_EVENTS = {
    "buy-price": (2, 100, None),
    "first-close": (52, 110, 60),
    "sell-price": (62, 110, None),
    "second-close": (112, 95, 120),
    "exit-price": (122, 105, None),
}
PAPER_LABEL = "Constructed Part 4.2 paper and P&L smoke"
BACKTEST_LABEL = "Constructed Part 4.2 SMA backtest evidence"
MICROS = 1_000_000


@contextmanager
def read_only(path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def utc_stamp(when):
    return when.isoformat(timespec="microseconds")


def quote(when, price, label):
    return {"id": "AAPL", "time": int(when.timestamp() * 1000), "price": price,
            "exchange": "NMS", "fixture": label}


def ensure_equal(actual, expected, label):
    if actual != expected:
        raise ValueError(f"Unexpected {label}: expected {expected!r}, observed {actual!r}")


def check_tape(connection, *, include_paper):
    expected = [(START + timedelta(minutes=minute, seconds=second), price, BACKTEST_LABEL, 1)
                for minute, second, price in TAPE]
    if include_paper:
        expected.extend((PAPER_START + timedelta(seconds=second), price, PAPER_LABEL, 0)
                        for second, price, _ in PAPER_EVENTS.values())
    rows = connection.execute(
        """SELECT a.arrival_sequence, a.receipt_time_utc, a.disposition,
                  a.correction_evidence AS arrival_correction, o.*
           FROM observation_arrivals a JOIN raw_observations o USING (observation_id)
           ORDER BY a.arrival_sequence"""
    ).fetchall()
    ensure_equal(len(rows), len(expected), "arrival count")
    ensure_equal(connection.execute("SELECT count(*) FROM raw_observations").fetchone()[0],
                 len(expected), "raw observation count")
    for sequence, (row, (when, price, label, delay)) in enumerate(zip(rows, expected), start=1):
        receipt = utc_stamp(when + timedelta(seconds=delay))
        observed = (row["arrival_sequence"], row["observation_id"], row["source"], row["symbol"],
                    row["source_time_ms"], row["source_time_utc"], row["source_timestamp_units"],
                    row["first_receipt_time_utc"], row["receipt_time_utc"], row["disposition"],
                    row["revision"], row["supersedes_id"], row["correction_evidence"],
                    row["arrival_correction"], json.loads(row["payload_json"]))
        expected_row = (sequence, sequence, SOURCE, "AAPL", int(when.timestamp() * 1000),
                        utc_stamp(when), "milliseconds_since_unix_epoch", receipt, receipt,
                        "inserted", 0, None, None, None, quote(when, price, label))
        ensure_equal(observed, expected_row, f"arrival {sequence}")


def verify_fresh_fixture(path):
    """Refuse to start the quote injector unless given the exact pristine test tape."""
    with read_only(path) as connection:
        check_tape(connection, include_paper=False)
        accounts = [tuple(row) for row in connection.execute(
            "SELECT id, cash_micros, shares FROM paper_accounts"
        )]
        ensure_equal(accounts, [("live", 1000 * MICROS, 0)], "starting account")
        for table in ("paper_orders", "paper_fills", "paper_pnl_history", "market_observations"):
            ensure_equal(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0],
                         0, f"starting {table} count")


def verify_completed_fixture(path):
    with read_only(path) as connection:
        check_tape(connection, include_paper=True)
        account = connection.execute(
            "SELECT id, cash_micros, shares FROM paper_accounts"
        ).fetchall()
        ensure_equal([tuple(row) for row in account], [("live", 1025 * MICROS, 0)], "ending account")
        orders = connection.execute(
            "SELECT id, account_id, side, quantity, status FROM paper_orders ORDER BY id"
        ).fetchall()
        ensure_equal([tuple(row) for row in orders],
                     [(1, "live", "buy", 4, "filled"), (2, "live", "sell", 1, "filled"),
                      (3, "live", "close", 3, "filled")], "simulated orders")
        fills = [dict(row) for row in connection.execute(
            """SELECT order_id, arrival_sequence, quantity, price_micros, executed_at
               FROM paper_fills ORDER BY order_id"""
        )]
        expected_fills = [
            (1, 10, 4, 100 * MICROS, utc_stamp(PAPER_START + timedelta(seconds=2))),
            (2, 12, 1, 110 * MICROS, utc_stamp(PAPER_START + timedelta(seconds=62))),
            (3, 14, 3, 105 * MICROS, utc_stamp(PAPER_START + timedelta(seconds=122))),
        ]
        ensure_equal([tuple(fill.values()) for fill in fills], expected_fills, "simulated fills")
        pnl = [dict(row) for row in connection.execute(
            """SELECT event_key, event, order_id, account_id, symbol, price_micros, cash_micros, shares,
                      realized_micros, unrealized_micros, total_micros
               FROM paper_pnl_history ORDER BY id"""
        )]
        expected_pnl = [
            ("fill:1", "fill", 1, 100, 600, 4, 0, 0, 0),
            ("candle:2026-11-10T15:00:00Z", "completed_candle", None, 110, 600, 4, 0, 40, 40),
            ("fill:2", "fill", 2, 110, 710, 3, 10, 30, 40),
            ("candle:2026-11-10T15:01:00Z", "completed_candle", None, 95, 710, 3, 10, -15, -5),
            ("fill:3", "fill", 3, 105, 1025, 0, 25, 0, 25),
        ]
        ensure_equal([tuple(row.values()) for row in pnl],
                     [(key, kind, order, "live", "AAPL", price * MICROS, cash * MICROS, shares,
                       str(realized * MICROS), str(unrealized * MICROS), str(total * MICROS))
                      for key, kind, order, price, cash, shares, realized, unrealized, total in expected_pnl],
                     "paper P&L history")
        ensure_equal(connection.execute("SELECT count(*) FROM market_observations").fetchone()[0],
                     0, "HTTP-polling observation count")

    return {"result": "PASS", "fixture": "Constructed AAPL data with both Yahoo workers disabled",
            "historical_arrivals_unchanged": len(TAPE), "new_paper_arrivals": len(PAPER_EVENTS),
            "paper_account": dict(account[0]), "fills": fills, "pnl_history": pnl}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = verify_completed_fixture(args.database)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("PASS: disposable SQLite has the original 9 historical arrivals, exactly 3 fills and 5 P&L events.")


if __name__ == "__main__":
    main()
