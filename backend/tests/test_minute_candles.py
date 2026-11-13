"""Official Prompt 07 fixtures are constructed TEST DATA, never production prices."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.db.database import Database
from backend.app.db.raw_observations import RawObservationRepository
from backend.app.services.minute_candles import MinuteCandleCalculator, form_one_minute_candles

UTC = timezone.utc
DAY = "2026-11-09"


def at(clock, day=DAY):
    return datetime.fromisoformat(f"{day}T{clock}+00:00")


def observation(clock, price, volume, day=DAY):
    return {"id": "AAPL", "time": str(int(at(clock, day).timestamp() * 1000)), "price": price,
            "day_volume": volume, "exchange": "NMS", "test_label": "CONSTRUCTED PROMPT 07 DATA"}


def insert(repo, clock, price, volume, *, receipt=None, day=DAY, **kwargs):
    return repo.record(observation(clock, price, volume, day), receipt or at(clock, day) + timedelta(milliseconds=1), **kwargs)


@pytest.fixture
def repo(tmp_path):
    database = Database(tmp_path / "prompt07.sqlite3")
    database.initialize()
    return RawObservationRepository(database)


def exact_example(repo):
    rows = [("14:59:59", 99, 1000), ("15:00:05", 100, 1004),
            ("15:00:20", 103, 1009), ("15:00:50", 101, 1011)]
    return [insert(repo, *row) for row in rows]


def calculated(repo, clock="15:01:00"):
    return MinuteCandleCalculator(repo).calculate(as_of=at(clock))


def target(result, timestamp="2026-11-09T15:00:00Z"):
    candle = next(item for item in result["candles"] if item["timestamp"] == timestamp)
    metadata = next(item for item in result["metadata"] if item["timestamp"] == timestamp)
    return candle, metadata


EXPECTED = {"timestamp": "2026-11-09T15:00:00Z", "open": 100, "high": 103, "low": 100, "close": 101, "volume": 11}


def test_instructor_exact_example_before_and_after_completion(repo):
    exact_example(repo)
    before, before_status = target(calculated(repo, "15:00:59"))
    after, after_status = target(calculated(repo))
    assert before == after == EXPECTED
    assert before_status["state"] == "forming" and not before_status["is_complete"]
    assert after_status["state"] == "complete" and after_status["is_complete"]
    assert after_status["volume_status"] == "known"
    assert set(after) == {"timestamp", "open", "high", "low", "close", "volume"}


def test_before_last_observation_no_future_price_or_volume_leaks(repo):
    exact_example(repo)
    candle, status = target(calculated(repo, "15:00:30"))
    assert candle == dict(EXPECTED, close=103, volume=9)
    assert status["observation_count"] == 2 and not status["is_complete"]


def test_middle_observation_arrives_late_then_completed_candle_is_revised(repo):
    insert(repo, "14:59:59", 99, 1000)
    insert(repo, "15:00:05", 100, 1004)
    insert(repo, "15:00:50", 101, 1011)
    insert(repo, "15:00:20", 103, 1009, receipt=at("15:01:10"))
    early, status = target(calculated(repo, "15:01:00"))
    late, late_status = target(calculated(repo, "15:01:11"))
    assert early == dict(EXPECTED, high=101)
    assert status["is_complete"] and late_status["is_complete"]
    assert late == EXPECTED
    assert target(calculated(repo, "15:01:00"))[0] == early


def test_duplicate_receipts_never_double_count_prices_or_volume(repo):
    exact_example(repo)
    repo.record(observation("15:00:20", 103, 1009), at("15:01:01"))
    candle, status = target(calculated(repo, "15:01:02"))
    assert candle == EXPECTED and status["observation_count"] == 3
    assert len(repo.arrivals()) == 5


def test_identified_correction_changes_high_without_adding_a_trade(repo):
    saved = exact_example(repo)
    insert(repo, "15:00:20", 104, 1010, receipt=at("15:01:10"), correction_of=saved[2].observation_id,
           correction_evidence="CONSTRUCTED TEST: explicit middle observation correction")
    assert target(calculated(repo))[0] == EXPECTED
    corrected, metadata = target(calculated(repo, "15:01:11"))
    assert corrected == dict(EXPECTED, high=104)
    assert metadata["observation_count"] == 3
    assert len(repo.arrivals()) == 5


def test_corrected_final_counter_recomputes_current_and_next_minute(repo):
    saved = exact_example(repo)
    insert(repo, "15:01:05", 102, 1015)
    insert(repo, "15:00:50", 101, 1012, receipt=at("15:02:00"), correction_of=saved[-1].observation_id,
           correction_evidence="CONSTRUCTED TEST: corrected boundary cumulative total")
    before = calculated(repo, "15:01:59")
    after = calculated(repo, "15:02:01")
    assert target(before)[0]["volume"] == 11
    assert target(after)[0]["volume"] == 12
    assert target(before, "2026-11-09T15:01:00Z")[0]["volume"] == 4
    assert target(after, "2026-11-09T15:01:00Z")[0]["volume"] == 3


def test_missing_baseline_means_unknown_not_first_counter_or_zero(repo):
    insert(repo, "15:00:05", 100, 1004)
    insert(repo, "15:00:50", 101, 1011)
    candle, status = target(calculated(repo))
    assert candle["volume"] is None
    assert status["volume_status"] == "unknown" and "missing_session_baseline" in status["volume_unknown_reasons"]


def test_counter_reset_marks_affected_minute_unknown_then_new_baseline_recovers(repo):
    insert(repo, "14:59:59", 99, 1000)
    insert(repo, "15:00:05", 100, 1004)
    insert(repo, "15:00:20", 103, 3)
    insert(repo, "15:00:50", 101, 5)
    insert(repo, "15:01:05", 102, 8)
    result = calculated(repo, "15:02:00")
    candle, status = target(result)
    assert candle["volume"] is None
    assert "cumulative_counter_reset_or_regression" in status["volume_unknown_reasons"]
    assert target(result, "2026-11-09T15:01:00Z")[0]["volume"] == 3


@pytest.mark.parametrize("bad_counter", [None, True, "not-a-counter", -1, 3.5])
def test_missing_or_invalid_counter_is_unknown(repo, bad_counter):
    insert(repo, "14:59:59", 99, 1000)
    insert(repo, "15:00:05", 100, bad_counter)
    assert target(calculated(repo))[0]["volume"] is None


def test_known_zero_is_preserved_as_zero(repo):
    insert(repo, "14:59:59", 99, 1000)
    insert(repo, "15:00:05", 100, 1000)
    candle, status = target(calculated(repo))
    assert candle["volume"] == 0 and status["volume_status"] == "known"


def test_source_order_not_arrival_order_controls_open_close(repo):
    insert(repo, "14:59:59", 99, 1000)
    insert(repo, "15:00:50", 101, 1011)
    insert(repo, "15:00:20", 103, 1009, receipt=at("15:01:00"))
    insert(repo, "15:00:05", 100, 1004, receipt=at("15:01:01"))
    assert target(calculated(repo, "15:01:02"))[0] == EXPECTED


def test_same_timestamp_distinct_prices_are_kept_with_stable_tie_order(repo):
    insert(repo, "14:59:59", 99, 1000)
    a = insert(repo, "15:00:05", 100, 1004)
    insert(repo, "15:00:05", 103, 1004)
    insert(repo, "15:00:05", 101, 1004)
    candle, status = target(calculated(repo))
    assert candle == dict(EXPECTED, volume=4)
    assert status["observation_count"] == 3
    insert(repo, "15:00:05", 99, 1004, receipt=at("15:01:01"), correction_of=a.observation_id,
           correction_evidence="CONSTRUCTED TEST: first tied observation correction")
    revised, _ = target(calculated(repo, "15:01:02"))
    assert revised["open"] == 99 and revised["close"] == 101


def test_ambiguous_same_timestamp_volume_is_unknown(repo):
    insert(repo, "14:59:59", 99, 1000)
    insert(repo, "15:00:05", 100, 1004)
    insert(repo, "15:00:05", 103, 1005)
    candle, status = target(calculated(repo))
    assert candle["volume"] is None
    assert "ambiguous_same_timestamp_volume" in status["volume_unknown_reasons"]


def test_missing_minute_gap_not_invented_or_filled_with_zero(repo):
    insert(repo, "14:59:59", 99, 1000)
    insert(repo, "15:02:05", 102, 1010)
    result = calculated(repo, "15:03:00")
    assert len(result["candles"]) == 2
    candle, status = target(result, "2026-11-09T15:02:00Z")
    assert candle["volume"] is None
    assert "unobserved_minute_gap" in status["volume_unknown_reasons"]


def test_no_cross_session_volume_baseline(repo):
    insert(repo, "23:59:59", 99, 1000)
    insert(repo, "00:00:05", 100, 1004, day="2026-11-10")
    same_local_date = MinuteCandleCalculator(repo).calculate(as_of=at("00:01:00", "2026-11-10"))
    assert target(same_local_date, "2026-11-10T00:00:00Z")[0]["volume"] == 4
    # New York midnight crosses the day/session-counter partition.
    insert(repo, "04:59:59", 100, 2000, day="2026-11-10")
    insert(repo, "05:00:05", 101, 2002, day="2026-11-10")
    next_local_date = MinuteCandleCalculator(repo).calculate(as_of=at("05:01:00", "2026-11-10"))
    assert target(next_local_date, "2026-11-10T05:00:00Z")[0]["volume"] is None


def test_calculation_is_read_only_repeatable_and_survives_reopen(repo):
    exact_example(repo)
    before = repo.arrivals()
    result = calculated(repo)
    reopened = RawObservationRepository(Database(repo.database.path))
    assert MinuteCandleCalculator(reopened).calculate(as_of=at("15:01:00")) == result
    assert repo.arrivals() == before
    assert calculated(repo) == result


def test_empty_journal_returns_no_synthetic_candles(repo):
    assert calculated(repo)["candles"] == [] and calculated(repo)["metadata"] == []


def test_naive_calculation_time_and_unconfigured_symbol_are_rejected(repo):
    with pytest.raises(ValueError, match="timezone"):
        MinuteCandleCalculator(repo).calculate(as_of=datetime(2026, 11, 9, 15))
    with pytest.raises(ValueError, match="AAPL"):
        form_one_minute_candles([], symbol="MSFT", as_of=at("15:00:00"))


def test_as_of_uses_available_arrival_prefix_when_receipt_clock_moves_back(repo):
    insert(repo, "14:59:59", 99, 1000)
    first = insert(repo, "15:00:05", 100, 1004, receipt=at("15:01:10"))
    insert(repo, "15:00:05", 101, 1005, receipt=at("15:01:00"), correction_of=first.observation_id,
           correction_evidence="CONSTRUCTED TEST: receipt clock moved backward")
    assert all(row["timestamp"] != "2026-11-09T15:00:00Z" for row in calculated(repo, "15:01:05")["candles"])
    candle, _ = target(calculated(repo, "15:01:11"))
    assert candle["open"] == 101 and candle["volume"] == 5


def test_corrected_source_time_moves_observation_to_correct_minute(repo):
    insert(repo, "14:59:59", 99, 1000)
    first = insert(repo, "15:00:05", 100, 1004)
    insert(repo, "15:01:05", 102, 1010)
    insert(repo, "15:01:10", 100, 1011, receipt=at("15:02:00"), correction_of=first.observation_id,
           correction_evidence="CONSTRUCTED TEST: source timestamp correction")
    assert target(calculated(repo, "15:01:59"))[0]["close"] == 100
    after = calculated(repo, "15:02:01")
    assert all(row["timestamp"] != "2026-11-09T15:00:00Z" for row in after["candles"])
    assert target(after, "2026-11-09T15:01:00Z")[0]["close"] == 100


def test_invalid_saved_price_is_not_silently_filled(repo):
    repo.record(observation("15:00:05", None, 1004), at("15:00:06"))
    with pytest.raises(ValueError, match="invalid price"):
        calculated(repo)
