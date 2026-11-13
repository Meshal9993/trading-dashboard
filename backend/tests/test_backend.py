from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.db.database import Database
from backend.app.db.observations import ObservationConflict, ObservationRepository
from backend.app.main import create_app
from backend.app.schemas.observation import Observation


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "nested" / "market.sqlite3")
    db.initialize()
    return db


@pytest.fixture
def repo(database):
    return ObservationRepository(database)


def observation(**changes):
    values = dict(
        symbol="AAPL", source="test", observation_id="candle-1",
        event_time=datetime(2026, 1, 1, 15, tzinfo=timezone.utc),
        receipt_time=datetime(2026, 1, 1, 15, 1, tzinfo=timezone.utc),
        open=100, high=110, low=90, close=105, volume=1000,
        revision=0, is_complete=False,
    )
    values.update(changes)
    return Observation(**values)


def test_startup_initializes_database_and_health(tmp_path):
    path = tmp_path / "data" / "market.sqlite3"
    app = create_app(path)
    assert not path.exists()
    with TestClient(app) as client:
        assert path.exists()
        assert client.get("/health").json() == {"status": "ok", "database": "ok"}
        assert client.get("/health").status_code == 200
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(market_observations)")}
    assert columns == set(Observation.model_fields)
    with TestClient(create_app(path)) as client:
        assert client.get("/health").status_code == 200


def test_environment_database_path(monkeypatch, tmp_path):
    path = tmp_path / "configured.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
    assert path.exists()


def test_health_reports_database_failure(tmp_path):
    path = tmp_path / "market.sqlite3"
    with TestClient(create_app(path)) as client:
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TABLE market_observations")
        assert client.get("/health").status_code == 503


def test_initialization_preserves_existing_data(database, repo):
    repo.store(observation())
    database.initialize()
    assert repo.current("AAPL", "test") == [observation()]


def test_duplicate_preserves_first_receipt(repo, database):
    assert repo.store(observation()) == "inserted"
    assert repo.store(observation(receipt_time=datetime.now(timezone.utc))) == "duplicate"
    assert repo.current("AAPL", "test") == [observation()]
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM market_observations").fetchone()[0] == 1


def test_revisions_and_late_revisions(repo, database):
    assert repo.store(observation()) == "inserted"
    assert repo.store(observation(revision=2, close=108, is_complete=True)) == "revised"
    assert repo.store(observation(revision=1, close=106)) == "stale"
    current = repo.current("AAPL", "test")
    assert len(current) == 1
    assert current[0].revision == 2
    assert current[0].close == 108
    assert current[0].is_complete is True
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM market_observations").fetchone()[0] == 3


def test_conflicting_revision_is_rejected_without_changes(repo):
    repo.store(observation())
    with pytest.raises(ObservationConflict, match="Conflicting payload"):
        repo.store(observation(close=106))
    assert repo.current("AAPL", "test") == [observation()]
    assert repo.store(observation(revision=1)) == "revised"


def test_identity_cannot_change_event_time(repo):
    repo.store(observation())
    with pytest.raises(ObservationConflict, match="event time"):
        repo.store(observation(revision=1, event_time=datetime.now(timezone.utc)))
    assert repo.current("AAPL", "test") == [observation()]


def test_out_of_order_candles_are_sorted_by_event_time(repo):
    later = observation(observation_id="later", event_time=datetime(2026, 1, 2, tzinfo=timezone.utc))
    repo.store(later)
    repo.store(observation())
    assert repo.current("AAPL", "test") == [observation(), later]


def test_identities_are_scoped_to_source_and_symbol(repo, database):
    for item in (observation(), observation(symbol="MSFT"), observation(source="other")):
        assert repo.store(item) == "inserted"
    assert len(repo.current("AAPL", "test")) == 1
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM market_observations").fetchone()[0] == 3


def test_timezone_normalization_makes_equivalent_times_duplicates(repo):
    repo.store(observation())
    equivalent = observation(event_time=datetime(2026, 1, 1, 10, tzinfo=timezone(timedelta(hours=-5))))
    assert repo.store(equivalent) == "duplicate"


def test_concurrent_duplicates_insert_once(repo, database):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repo.store(observation()), range(8)))
    assert results.count("inserted") == 1
    assert results.count("duplicate") == 7


def test_concurrent_revisions_keep_highest_current(repo):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda rev: repo.store(observation(revision=rev)), [3, 1, 5, 0, 4, 2]))
    assert repo.current("AAPL", "test")[0].revision == 5


@pytest.mark.parametrize("changes", [
    {"symbol": " "}, {"source": ""}, {"observation_id": ""},
    {"event_time": datetime(2026, 1, 1)}, {"receipt_time": datetime(2026, 1, 1)},
    {"revision": 2**63}, {"volume": 2**63}, {"revision": -1}, {"revision": True}, {"volume": -1}, {"volume": 1.5},
    {"high": 95}, {"low": 106}, {"open": -1}, {"close": float("nan")},
    {"high": float("inf")}, {"is_complete": "false"},
])
def test_invalid_observations_rejected(changes):
    with pytest.raises(ValidationError):
        observation(**changes)


def test_database_constraints_reject_invalid_direct_write(database, repo):
    repo.store(observation())
    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute("UPDATE market_observations SET volume=-1")
    assert repo.current("AAPL", "test") == [observation()]


def test_subsecond_event_ordering(repo):
    early = observation(observation_id="early")
    late = observation(observation_id="late", event_time=early.event_time + timedelta(microseconds=1))
    repo.store(late)
    repo.store(early)
    assert repo.current("AAPL", "test") == [early, late]
