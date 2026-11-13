"""Prompt 06 constructed test data; never imported into the application."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from backend.app.db.database import Database
from backend.app.db.raw_observations import CorrectionConflict, RawObservationRepository
from backend.app.main import create_app
from backend.app.services.yahoo_stream import check_subscription

NOW = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)


def message(**changes):
    return dict({"id": "AAPL", "time": str(int(NOW.timestamp() * 1000)), "price": 100.25,
                 "day_volume": "12345", "exchange": "NMS", "extra_source_field": {"kept": True}}, **changes)


@pytest.fixture
def repo(tmp_path):
    database = Database(tmp_path / "disposable.sqlite3")
    database.initialize()
    return RawObservationRepository(database)


def test_exact_repeat_is_suppressed_but_every_receipt_is_traceable(repo):
    first = repo.record(message(), NOW)
    second = repo.record(dict(reversed(list(message().items()))), NOW + timedelta(seconds=1))
    assert first.disposition == "inserted" and second.disposition == "duplicate"
    assert first.observation_id == second.observation_id
    assert [row["arrival_sequence"] for row in repo.arrivals()] == [1, 2]
    assert repo.arrivals()[1]["receipt_time_utc"] != repo.arrivals()[0]["receipt_time_utc"]
    assert repo.arrivals()[0]["first_receipt_time_utc"] == repo.arrivals()[1]["first_receipt_time_utc"]
    with repo.database.connect() as db:
        assert db.execute("SELECT count(*) FROM raw_observations").fetchone()[0] == 1
    assert repo.arrivals()[0]["source_fields"] == message()


def test_changed_message_at_same_timestamp_is_independent_not_an_assumed_revision(repo):
    a = repo.record(message(), NOW)
    b = repo.record(message(price=101), NOW)
    assert a.observation_id != b.observation_id
    assert b.disposition == "inserted" and b.revision == 0
    assert all(row["supersedes_id"] is None for row in repo.arrivals())
    assert len({row["source_time_ms"] for row in repo.arrivals()}) == 1


def test_explicitly_identified_correction_retains_prior_values_and_proof(repo):
    a = repo.record(message(), NOW)
    b = repo.record(message(price=102), NOW + timedelta(seconds=1),
                    correction_of=a.observation_id, correction_evidence="TEST adapter: explicit correction reference")
    duplicate = repo.record(message(price=102), NOW + timedelta(seconds=2),
                            correction_of=a.observation_id, correction_evidence="TEST adapter: explicit correction reference")
    c = repo.record(message(price=100.25), NOW + timedelta(seconds=3),
                    correction_of=b.observation_id, correction_evidence="TEST correction: reversion to original price")
    assert (b.disposition, b.revision) == ("revision", 1)
    assert duplicate.observation_id == b.observation_id and duplicate.disposition == "duplicate"
    assert c.revision == 2 and c.observation_id != a.observation_id
    rows = repo.arrivals()
    assert rows[0]["source_fields"]["price"] == 100.25
    assert rows[1]["supersedes_id"] == a.observation_id
    assert rows[1]["correction_evidence"].startswith("TEST")


def test_late_arrival_preserves_source_time_and_durable_receipt_order(repo):
    repo.record(message(), NOW)
    repo.record(message(time=str(int((NOW - timedelta(minutes=1)).timestamp() * 1000))), NOW + timedelta(seconds=1))
    rows = repo.arrivals()
    assert rows[1]["arrival_sequence"] > rows[0]["arrival_sequence"]
    assert rows[1]["source_time_ms"] < rows[0]["source_time_ms"]
    assert [row["arrival_sequence"] for row in repo.arrivals(after_sequence=1)] == [2]


def test_equal_or_backward_receipt_clocks_do_not_reorder_journal(repo):
    for offset in [0, 0, -1]:
        repo.record(message(), NOW + timedelta(seconds=offset))
    assert [row["arrival_sequence"] for row in repo.arrivals()] == [1, 2, 3]


def test_concurrent_duplicate_recording_is_atomic(repo):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repo.record(message(), NOW), range(8)))
    assert sum(row.disposition == "inserted" for row in results) == 1
    assert len({row.observation_id for row in results}) == 1
    assert [row["arrival_sequence"] for row in repo.arrivals()] == list(range(1, 9))


def test_backend_process_restart_retains_records_and_continues_sequence(repo):
    repo.record(message(), NOW)
    repo.record(message(), NOW + timedelta(seconds=1))
    repo.record(message(price=101), NOW + timedelta(seconds=2))
    before = repo.arrivals()
    # A different interpreter process starts/reopens FastAPI, reads the journal,
    # and records another arrival after lifespan initialization.
    script = '''
import json,sys
from datetime import datetime,timezone
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.db.raw_observations import RawObservationRepository
from pathlib import Path
with TestClient(create_app(Path(sys.argv[1]),feed_enabled=False)) as client:
    assert client.get('/health').status_code==200
    repo=RawObservationRepository(client.app.state.database)
    before=repo.arrivals()
    result=repo.record(before[0]['source_fields'],datetime.now(timezone.utc))
    print(json.dumps({'before':before,'new_sequence':result.arrival_sequence,'disposition':result.disposition}))
'''
    output = subprocess.run([sys.executable, "-B", "-c", script, str(repo.database.path)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=True)
    after = json.loads(output.stdout)
    assert after["before"] == before
    assert after["new_sequence"] == 4 and after["disposition"] == "duplicate"
    assert len(repo.arrivals()) == 4


def test_schema_addition_preserves_candle_history(repo):
    from backend.app.db.observations import ObservationRepository
    from backend.app.schemas.observation import Observation
    candles = ObservationRepository(repo.database)
    item = Observation(symbol="AAPL", source="test", observation_id="one", event_time=NOW,
                       receipt_time=NOW, open=100, high=105, low=99, close=101,
                       volume=10, revision=0, is_complete=True)
    candles.store(item)
    repo.record(message(), NOW)
    repo.database.initialize()
    assert candles.current("AAPL", "test") == [item]
    with TestClient(create_app(repo.database.path, feed_enabled=False)) as client:
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("kwargs", [
    {"correction_of": 99, "correction_evidence": "TEST missing reference"},
    {"correction_of": 1}, {"correction_evidence": "no reference"},
])
def test_invalid_correction_is_atomic(repo, kwargs):
    with pytest.raises((ValueError, CorrectionConflict)):
        repo.record(message(), NOW, **kwargs)
    assert repo.arrivals() == []


def test_forking_or_cross_symbol_corrections_are_rejected(repo):
    original = repo.record(message(), NOW)
    repo.record(message(price=102), NOW, correction_of=original.observation_id, correction_evidence="TEST correction")
    with pytest.raises(CorrectionConflict):
        repo.record(message(price=103), NOW, correction_of=original.observation_id, correction_evidence="TEST conflicting branch")
    with pytest.raises(CorrectionConflict):
        repo.record(message(id="MSFT"), NOW, correction_of=original.observation_id, correction_evidence="TEST wrong symbol")
    assert len(repo.arrivals()) == 2


def test_raw_types_and_unknown_fields_are_not_discarded(repo):
    repo.record(message(), NOW)
    repo.record(message(time=int(message()["time"])), NOW)
    rows = repo.arrivals()
    assert isinstance(rows[0]["source_fields"]["time"], str)
    assert isinstance(rows[1]["source_fields"]["time"], int)
    assert rows[0]["source_fields"]["extra_source_field"] == {"kept": True}


def test_stream_receiver_records_every_valid_callback(repo):
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def subscribe(self, symbols):
            pass
        async def listen(self, callback):
            for item in [message(), message(), message(price=101)]:
                await callback(item)
            await asyncio.Future()
        async def close(self):
            pass
    result = asyncio.run(check_subscription(client_factory=Client, recorder=repo, max_quotes=3))
    assert result["receipt_verified"] and result["recording_enabled"]
    assert result["persistence_error"] is None
    assert [row["disposition"] for row in repo.arrivals()] == ["inserted", "duplicate", "inserted"]


def test_stream_reports_persistence_failure_without_claiming_storage_success():
    class BrokenRecorder:
        def record(self, *args):
            raise OSError("TEST database unavailable")
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def subscribe(self, symbols):
            pass
        async def listen(self, callback):
            await callback(message())
            await asyncio.Future()
        async def close(self):
            pass
    result = asyncio.run(check_subscription(client_factory=Client, recorder=BrokenRecorder()))
    assert result["receipt_verified"] and result["persistence_error"]
    assert result["outcome"] == "persistence_failed" and result["recorded_arrivals"] == []


def test_legacy_candle_only_database_is_extended_without_data_loss(tmp_path):
    from backend.app.db.database import SCHEMA
    from backend.app.db.observations import ObservationRepository
    from backend.app.schemas.observation import Observation
    database = Database(tmp_path / "legacy.sqlite3")
    with database.connect() as db:
        db.executescript(SCHEMA)
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='raw_observations'").fetchone()
    item = Observation(symbol="AAPL", source="test", observation_id="legacy", event_time=NOW,
                       receipt_time=NOW, open=100, high=105, low=99, close=101,
                       volume=10, revision=0, is_complete=True)
    ObservationRepository(database).store(item)
    database.initialize()
    assert ObservationRepository(database).current("AAPL", "test") == [item]
    assert RawObservationRepository(database).record(message(), NOW).arrival_sequence == 1


@pytest.mark.parametrize("changes", [{"time": "bad"}, {"time": True}, {"id": " "}, {"price": float("nan")}])
def test_invalid_raw_payload_does_not_leave_partial_journal_rows(repo, changes):
    with pytest.raises((ValueError, TypeError)):
        repo.record(message(**changes), NOW)
    assert repo.arrivals() == []


def test_naive_receipt_timestamp_rejected_without_writes(repo):
    with pytest.raises(ValueError, match="timezone"):
        repo.record(message(), NOW.replace(tzinfo=None))
    assert repo.arrivals() == []
