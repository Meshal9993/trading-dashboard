"""Prompt 09A checks using constructed prices and a disposable replay journal."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.db.raw_observations import RawObservationRepository
from backend.app.main import create_app
from backend.app.services.sma import with_sma


START = datetime(2026, 11, 9, 15, tzinfo=timezone.utc)
PERIODS = {"sma_short": 2, "sma_long": 3}
CLOSES = [13, 10, 7, 13, 19, 7, 1]
EXPECTED = [
    (None, None, None),
    (11.5, None, None),
    (8.5, 10, None),
    (10, 10, None),
    (16, 13, "up"),
    (13, 13, None),
    (4, 9, "down"),
]


def time_at(minute, second=0):
    return START + timedelta(minutes=minute, seconds=second)


def quote(repository, minute, close, *, receipt=None, **correction):
    event = time_at(minute, 5)
    return repository.record(
        {"id": "AAPL", "time": str(int(event.timestamp() * 1000)), "price": close,
         "day_volume": str(1000 + minute), "exchange": "NMS"},
        receipt if receipt is not None else time_at(minute + 1), **correction,
    )


def readings(envelope):
    return [(point["short"], point["long"], point["crossover"])
            for point in envelope["sma"]["points"]]


def test_instructor_example_history_matches_every_replay_prefix_with_revision(tmp_path):
    app = create_app(tmp_path / "sma.sqlite3", feed_enabled=False, stream_enabled=False,
                     stream_clock=lambda: time_at(7))
    with TestClient(app) as client:
        repository = RawObservationRepository(app.state.database)
        arrivals = [quote(repository, i, close) for i, close in enumerate(CLOSES)]
        revision = quote(repository, 4, 1, receipt=time_at(8),
                         correction_of=arrivals[4].observation_id,
                         correction_evidence="Constructed Prompt 09A correction")

        batch = client.get("/history", params={**PERIODS, "as_of": time_at(7).isoformat()})
        assert batch.status_code == 200, batch.text
        before = batch.json()
        assert [candle["close"] for candle in before["candles"]] == CLOSES
        assert readings(before) == EXPECTED
        assert [point["timestamp"] for point in before["sma"]["points"]] == [
            candle["timestamp"] for candle in before["candles"]
        ]

        query = {**PERIODS, "start": START.isoformat(), "end": time_at(8).isoformat(), "cursor": 0}
        for i, arrival in enumerate(arrivals):
            response = client.get("/replay/frame", params=query)
            assert response.status_code == 200, response.text
            frame = response.json()
            assert frame["replay"]["cursor"] == arrival.arrival_sequence
            assert frame["sma"]["points"] == before["sma"]["points"][:i + 1]
            query.update(cursor=frame["replay"]["cursor"], high_water=frame["replay"]["high_water"])

        with client.websocket_connect("/ws/candles?" + urlencode(PERIODS)) as socket:
            frame = socket.receive_json()
            assert frame["sma"] == before["sma"]

        corrected = client.get("/replay/frame", params=query).json()
        assert corrected["replay"]["cursor"] == revision.arrival_sequence
        assert corrected["candles"][4]["close"] == 1
        assert readings(corrected) == EXPECTED[:4] + [(7, 7, None), (4, 7, None), (4, 3, "up")]
        assert client.get("/replay/frame", params={**query, "advance": "false"}).json()["sma"] == before["sma"]


def test_forming_and_missing_intervals_do_not_fill_sma_windows(tmp_path):
    app = create_app(tmp_path / "sma.sqlite3", feed_enabled=False, stream_enabled=False)
    with TestClient(app) as client:
        repository = RawObservationRepository(app.state.database)
        # Skip minute 1 entirely and reveal minute 5 while its candle is forming.
        for minute, close in [(0, 13), (2, 10), (3, 7), (4, 13)]:
            quote(repository, minute, close)
        quote(repository, 5, 19, receipt=time_at(5, 10))
        forming = client.get("/history", params={**PERIODS, "as_of": time_at(5, 10).isoformat()}).json()
        assert len(forming["candles"]) == 5
        assert readings(forming) == EXPECTED[:4] + [(None, None, None)]
        complete = client.get("/history", params={**PERIODS, "as_of": time_at(6).isoformat()}).json()
        assert readings(complete) == EXPECTED[:5]


def test_crossovers_use_precision_beyond_display_rounding():
    # All prices round to the same displayed value at two decimal places.
    closes = [1.00001, 1, 1.00001, 1]
    candles = [{"timestamp": str(i), "close": close} for i, close in enumerate(closes)]
    snapshot = {"candles": candles, "metadata": [{"timestamp": row["timestamp"], "is_complete": True}
                                                for row in candles]}
    assert [point[2] for point in readings(with_sma(snapshot, (1, 2)))] == [None, None, "up", "down"]
    assert "sma" not in snapshot


@pytest.mark.parametrize("periods", [
    {"sma_short": 2}, {"sma_long": 3}, {"sma_short": 0, "sma_long": 3},
    {"sma_short": 3, "sma_long": 3}, {"sma_short": 4, "sma_long": 3},
    {"sma_short": 1.5, "sma_long": 3}, {"sma_short": 1, "sma_long": 2001},
])
def test_history_rejects_invalid_sma_periods(tmp_path, periods):
    with TestClient(create_app(tmp_path / "sma.sqlite3", feed_enabled=False, stream_enabled=False)) as client:
        assert client.get("/history", params=periods).status_code == 422


def test_stream_rejects_invalid_sma_periods(tmp_path):
    with TestClient(create_app(tmp_path / "sma.sqlite3", feed_enabled=False, stream_enabled=False)) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/candles?sma_short=3&sma_long=2"):
                pytest.fail("Invalid periods must be rejected before accepting the socket")
    assert exc.value.code == 1008
