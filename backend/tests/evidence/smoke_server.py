"""Loopback-only, offline quote injector for the disposable Part 4.2 smoke run."""
import asyncio
import os
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException

from backend.app.main import create_app
from backend.tests.evidence.smoke_database import (
    PAPER_EVENTS, PAPER_LABEL, PAPER_START, quote, verify_fresh_fixture,
)

database = Path(os.environ["PAPER_EVIDENCE_DATABASE"]).resolve(strict=True)
if database != Path(os.environ["DATABASE_PATH"]).resolve(strict=True):
    raise RuntimeError("The smoke backend requires matching explicit fixture database paths")
run_token = os.environ["SMOKE_RUN_TOKEN"]
if not run_token:
    raise RuntimeError("The smoke backend requires a run token")
verify_fresh_fixture(database)
now = PAPER_START
pending_events = iter(PAPER_EVENTS)
next_event = next(pending_events, None)
event_lock = asyncio.Lock()
app = create_app(database, feed_enabled=False, stream_enabled=False, stream_clock=lambda: now)


@app.get("/health/smoke-ready")
async def fixture_ready():
    return {"token": run_token}


@app.post("/test/smoke/price/{event}")
async def instructor_event(event: str):
    global now, next_event
    async with event_lock:
        if event != next_event:
            raise HTTPException(409, detail=f"Expected smoke event {next_event!r}")
        seconds, price, completion = PAPER_EVENTS[event]
        now = PAPER_START + timedelta(seconds=seconds)
        recorded = await app.state.live_feed.accept(quote(now, price, PAPER_LABEL))
        if recorded is None or recorded.disposition != "inserted":
            raise RuntimeError("Constructed quote was not recorded")
        if completion is not None:
            now = PAPER_START + timedelta(seconds=completion)
            await asyncio.to_thread(app.state.paper.mark_live)
        next_event = next(pending_events, None)
        return {"constructed_price": price, "arrival_sequence": recorded.arrival_sequence}
