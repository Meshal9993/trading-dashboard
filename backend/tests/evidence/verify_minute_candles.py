"""Verify official Prompt 07 with constructed instructor input in disposable SQLite."""
import json
from datetime import timedelta
from pathlib import Path
import sys
import tempfile

from backend.app.db.database import Database
from backend.app.db.raw_observations import RawObservationRepository
from backend.app.services.minute_candles import MinuteCandleCalculator
from backend.tests.test_minute_candles import EXPECTED, at, exact_example, insert, target


def run(output):
    path = Path(tempfile.mkdtemp(prefix="prompt07-test-")) / "candles.sqlite3"
    database = Database(path)
    database.initialize()
    repo = RawObservationRepository(database)
    exact_example(repo)
    before_rows = repo.arrivals()
    calculate = MinuteCandleCalculator(repo).calculate
    forming = target(calculate(as_of=at("15:00:59")))
    complete = target(calculate(as_of=at("15:01:00")))
    assert forming[0] == complete[0] == EXPECTED
    assert not forming[1]["is_complete"] and complete[1]["is_complete"]
    assert repo.arrivals() == before_rows

    late_db = Database(path.parent / "late.sqlite3")
    late_db.initialize()
    late_repo = RawObservationRepository(late_db)
    insert(late_repo, "14:59:59", 99, 1000)
    insert(late_repo, "15:00:05", 100, 1004)
    insert(late_repo, "15:00:50", 101, 1011)
    insert(late_repo, "15:00:20", 103, 1009, receipt=at("15:01:10"))
    late_calc = MinuteCandleCalculator(late_repo).calculate
    before_late = target(late_calc(as_of=at("15:01:00")))
    after_late = target(late_calc(as_of=at("15:01:11")))
    assert before_late[0] == dict(EXPECTED, high=101)
    assert after_late[0] == EXPECTED

    missing_db = Database(path.parent / "unknown.sqlite3")
    missing_db.initialize()
    missing = RawObservationRepository(missing_db)
    insert(missing, "15:00:05", 100, 1004)
    insert(missing, "15:00:50", 101, 1011)
    unknown = target(MinuteCandleCalculator(missing).calculate(as_of=at("15:01:00")))
    assert unknown[0]["volume"] is None
    reset_db = Database(path.parent / "reset.sqlite3")
    reset_db.initialize()
    reset = RawObservationRepository(reset_db)
    insert(reset, "14:59:59", 99, 1000)
    insert(reset, "15:00:05", 100, 1004)
    insert(reset, "15:00:20", 103, 3)
    reset_result = target(MinuteCandleCalculator(reset).calculate(as_of=at("15:01:00")))
    assert reset_result[0]["volume"] is None
    result = {"result": "PASS", "input_kind": "CONSTRUCTED INSTRUCTOR TEST DATA; NOT LIVE YAHOO PRICES",
              "database_path": str(path), "expected": EXPECTED,
              "forming": {"candle": forming[0], "metadata": forming[1]},
              "complete": {"candle": complete[0], "metadata": complete[1]},
              "before_late_arrival": {"candle": before_late[0], "metadata": before_late[1]},
              "after_late_arrival": {"candle": after_late[0], "metadata": after_late[1]},
              "missing_baseline": {"candle": unknown[0], "metadata": unknown[1]},
              "reset_counter": {"candle": reset_result[0], "metadata": reset_result[1]},
              "saved_journal_unchanged": repo.arrivals() == before_rows,
              "input_journal": before_rows}
    output.mkdir(parents=True, exist_ok=True)
    (output / "instructor-example.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "input_journal"}, indent=2))


if __name__ == "__main__":
    run(Path(sys.argv[1]))
