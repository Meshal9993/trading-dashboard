"""Offline Prompt 09D browser fixture. Run only against a disposable SQLite file."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from backend.app.main import create_app

START = datetime(2026, 11, 9, 15, tzinfo=timezone.utc)
now = START
app = create_app(Path(os.environ["PAPER_EVIDENCE_DATABASE"]), feed_enabled=False,
                 stream_enabled=False, stream_clock=lambda: now)


@app.post("/test/pnl-event/{event}")
async def instructor_event(event: Literal["buy-price", "first-close", "sell-price", "second-close", "exit-price"]):
    global now
    second, price, completion = {
        "buy-price": (2, 100, None),
        "first-close": (52, 110, 60),
        "sell-price": (62, 110, None),
        "second-close": (112, 95, 120),
        "exit-price": (122, 105, None),
    }[event]
    now = START + timedelta(seconds=second)
    recorded = await app.state.live_feed.accept({
        "id": "AAPL", "time": int(now.timestamp() * 1000), "price": price,
        "exchange": "NMS", "fixture": "Constructed Prompt 09D instructor example",
    })
    if completion is not None:
        now = START + timedelta(seconds=completion)
        await asyncio.to_thread(app.state.paper.mark_live)
    return {"constructed_price": price, "arrival_sequence": recorded.arrival_sequence}
