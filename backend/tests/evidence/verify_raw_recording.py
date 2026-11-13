"""Prompt 06 disposable-db verification. All constructed messages are TEST DATA."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile

from fastapi.testclient import TestClient

from backend.app.db.raw_observations import RawObservationRepository
from backend.app.main import create_app


def run():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="prompt06-test-"))
    path = folder / "disposable.sqlite3"
    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    original = {"id": "AAPL", "price": 100, "time": str(int(now.timestamp() * 1000)),
                "day_volume": "1000", "verification_label": "CONSTRUCTED TEST DATA"}
    with TestClient(create_app(path, feed_enabled=False)) as client:
        repo = RawObservationRepository(client.app.state.database)
        a = repo.record(original, now)
        repo.record(original, now + timedelta(seconds=1))
        b = repo.record(dict(original, price=101), now + timedelta(seconds=2))
        repo.record(dict(original, price=99, time=str(int((now - timedelta(minutes=1)).timestamp() * 1000))), now + timedelta(seconds=3))
        revised = dict(original, price=102)
        repo.record(revised, now + timedelta(seconds=4), correction_of=b.observation_id,
                    correction_evidence="CONSTRUCTED TEST DATA: adapter explicitly identifies B as correction target")
        repo.record(revised, now + timedelta(seconds=5), correction_of=b.observation_id,
                    correction_evidence="CONSTRUCTED TEST DATA: adapter explicitly identifies B as correction target")
        before = repo.arrivals()
        with repo.database.connect() as db:
            observations = [dict(row) for row in db.execute("SELECT * FROM raw_observations ORDER BY observation_id")]
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    script = '''
import json,sys
from pathlib import Path
from datetime import datetime,timezone
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.db.raw_observations import RawObservationRepository
with TestClient(create_app(Path(sys.argv[1]),feed_enabled=False)) as client:
    repo=RawObservationRepository(client.app.state.database)
    before=repo.arrivals()
    recorded=repo.record(before[0]['source_fields'],datetime.now(timezone.utc))
    print(json.dumps({'before':before,'arrivals_after':repo.arrivals(),'new_sequence':recorded.arrival_sequence,'disposition':recorded.disposition}))
'''
    process = subprocess.run([sys.executable, "-B", "-c", script, str(path)],
                             capture_output=True, text=True, check=True)
    restarted = json.loads(process.stdout)
    assert restarted["before"] == before
    assert restarted["new_sequence"] == 7 and restarted["disposition"] == "duplicate"
    dispositions = [row["disposition"] for row in before]
    assert dispositions == ["inserted", "duplicate", "inserted", "inserted", "revision", "duplicate"]
    assert len(observations) == 4 and integrity == "ok"
    assert before[2]["source_time_ms"] == before[0]["source_time_ms"]
    assert before[2]["supersedes_id"] is None and before[3]["source_time_ms"] < before[0]["source_time_ms"]
    assert before[4]["supersedes_id"] == b.observation_id and before[4]["revision"] == 1
    result = {"result": "PASS", "input_kind": "CONSTRUCTED TEST DATA, NOT YAHOO MARKET EVIDENCE",
              "database_path": str(path), "captured_at_utc": datetime.now(timezone.utc).isoformat(),
              "expected": {"distinct_observations": 4, "arrivals_before_restart": 6, "next_sequence_after_restart": 7,
                           "dispositions_before_restart": dispositions},
              "observed": {"distinct_observations": len(observations), "arrivals_before_restart": len(before),
                           "next_sequence_after_restart": restarted["new_sequence"], "sqlite_integrity": integrity,
                           "restart_preserved_all_fields": restarted["before"] == before},
              "raw_observation_rows": observations, "arrival_rows_before_restart": before,
              "arrival_rows_after_restart": restarted["arrivals_after"],
              "restart_method": "First FastAPI lifespan closed; a new interpreter process initialized FastAPI with the same SQLite path and polling disabled."}
    (output / "disposable-recording.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in ("raw_observation_rows", "arrival_rows_before_restart", "arrival_rows_after_restart")}, indent=2))


if __name__ == "__main__":
    run()
