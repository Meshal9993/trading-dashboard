"""Local evidence entry point only: real Yahoo quotes plus an explicitly mocked outage."""
import json
import os
from pathlib import Path

from backend.app.main import create_app
from backend.app.services.live_feed import YahooQuoteConnection

CONTROL = Path(os.environ['PROMPT08_CONTROL'])


class EvidenceConnection(YahooQuoteConnection):
    def paused(self):
        return json.loads(CONTROL.read_text()).get('paused', False)

    async def open(self):
        if self.paused():
            raise ConnectionError('TEST-ONLY MOCKED upstream interruption')
        await super().open()

    async def receive(self):
        message = await super().receive()
        if self.paused():
            raise ConnectionError('TEST-ONLY MOCKED upstream interruption')
        return message


app = create_app(stream_factory=EvidenceConnection)
