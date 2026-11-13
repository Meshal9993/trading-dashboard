"""LOCAL VERIFICATION ONLY. Never use this entry point as the production app.

Failure/replay injection routes exist only here, bound to loopback by the operator.
Replay uses an actual Yahoo capture; this module never synthesizes market prices.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import HTTPException

from backend.app.main import create_app
from backend.app.schemas.observation import Observation
from backend.app.services.yahoo import ChartBatch, YahooChartClient

MODE_PATH = Path(os.environ["EVIDENCE_MODE_PATH"])
CAPTURE_PATH = Path(os.environ["EVIDENCE_CAPTURE_PATH"])


class EvidenceProvider:
    async def fetch(self):
        mode = json.loads(MODE_PATH.read_text())
        if mode["mode"] == "failure":
            # Exercise the real HTTP adapter and its failure path with a mocked 503.
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(503, json={"evidence": "mocked Yahoo outage"})
            )) as client:
                return await YahooChartClient(client).fetch()
        if mode["mode"] == "replay":
            capture = json.loads(CAPTURE_PATH.read_text())
            items = [Observation.model_validate(row) for row in capture["observations"]]
            count = mode.get("count", len(items))
            return ChartBatch(items[:count], {}, datetime.now(timezone.utc))
        async with httpx.AsyncClient() as client:
            return await YahooChartClient(client).fetch()


app = create_app(provider=EvidenceProvider(), feed_enabled=True, stream_enabled=False)


@app.post("/__evidence/poll")
async def poll():
    if not hasattr(app.state, "feed"):
        raise HTTPException(503)
    await app.state.feed.poll_once()
    return app.state.feed.status()
