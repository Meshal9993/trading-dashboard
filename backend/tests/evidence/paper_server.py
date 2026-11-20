"""Test-only server for Prompt 09C browser evidence. Both Yahoo workers are disabled."""
import os
from pathlib import Path
from typing import Literal

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from backend.app.main import create_app
from backend.app.services.live_feed import utc_now

app = create_app(Path(os.environ["PAPER_EVIDENCE_DATABASE"]), feed_enabled=False, stream_enabled=False)
drop_first_order_response = os.environ.get("PAPER_EVIDENCE_DROP_FIRST_RESPONSE") == "1"


@app.middleware("http")
async def simulate_lost_response(request: Request, call_next):
    global drop_first_order_response
    response = await call_next(request)
    if (drop_first_order_response and request.method == "POST"
            and request.url.path == "/paper/accounts/live/orders" and response.status_code == 200):
        drop_first_order_response = False
        return JSONResponse({"detail": "Constructed lost response after accepting the order"}, status_code=503)
    return response


@app.post("/test/paper-quote")
async def constructed_quote(price: Literal["100", "110", "105"] = Query(...)):
    source_time = utc_now()
    receipt = await app.state.live_feed.accept({
        "id": "AAPL", "price": int(price), "time": int(source_time.timestamp() * 1_000),
        "exchange": "NMS", "day_volume": "10", "fixture": "Prompt 09C synthetic verification only",
    })
    return {"arrival_sequence": receipt.arrival_sequence, "constructed_price": int(price)}
