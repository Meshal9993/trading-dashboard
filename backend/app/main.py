"""FastAPI entry point. Run from the repository root with one worker."""
import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import httpx
import anyio
from fastapi import Query, FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from backend.app.db.database import Database
from backend.app.db.observations import ObservationRepository
from backend.app.db.raw_observations import RawObservationRepository
from backend.app.services.replay import ReplayService
from backend.app.services.history import HistoryService
from backend.app.services.live_feed import LiveCandleFeed, YahooQuoteConnection, utc_now
from backend.app.services.feed import MarketFeed
from backend.app.services.yahoo import SOURCE, YahooChartClient

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "market.sqlite3"


def create_app(database_path: Optional[Path] = None, provider=None,
               feed_enabled: Optional[bool] = None, poll_seconds: Optional[float] = None,
               stream_enabled: Optional[bool] = None, stream_factory=None, stream_clock=utc_now) -> FastAPI:
    database = Database(database_path if database_path is not None else Path(
        os.environ.get("DATABASE_PATH", str(DEFAULT_DATABASE_PATH))
    ))
    repository = ObservationRepository(database)
    history_service = HistoryService(RawObservationRepository(database), repository)
    enabled = feed_enabled if feed_enabled is not None else os.environ.get("YAHOO_FEED_ENABLED", "true").lower() == "true"
    interval = poll_seconds if poll_seconds is not None else float(os.environ.get("YAHOO_POLL_SECONDS", "30"))

    streaming = stream_enabled if stream_enabled is not None else os.environ.get("YAHOO_STREAM_ENABLED", "true").lower() == "true"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.initialize()
        async with httpx.AsyncClient() as client:
            feed = MarketFeed(provider if provider is not None else YahooChartClient(client), repository, interval)
            app.state.feed = feed
            await feed.restore()
            feed.state = "starting" if enabled else "disabled"
            live = LiveCandleFeed(RawObservationRepository(database), history_service, feed,
                                 enabled=streaming, connection_factory=stream_factory or YahooQuoteConnection,
                                 clock=stream_clock)
            app.state.live_feed = live
            await live.restore()
            feed.on_change = live.signal
            task = asyncio.create_task(feed.run()) if enabled else None
            stream_task = asyncio.create_task(live.run()) if streaming else None
            try:
                yield
            finally:
                for running in (task, stream_task):
                    if running:
                        running.cancel()
                await asyncio.gather(*(running for running in (task, stream_task) if running), return_exceptions=True)

    application = FastAPI(title="Trading Dashboard API", lifespan=lifespan)
    application.state.database = database
    application.state.repository = repository

    @application.get("/health")
    def health():
        try:
            database.check_health()
        except sqlite3.Error as error:
            raise HTTPException(status_code=503, detail="Database unavailable") from error
        return {"status": "ok", "database": "ok"}

    @application.get("/feed/status")
    async def feed_status():
        return application.state.feed.status()

    @application.get("/observations")
    async def observations():
        rows = await asyncio.to_thread(repository.current, "AAPL", SOURCE)
        return {"status": application.state.feed.status(),
                "observations": [row.model_dump(mode="json") for row in rows[-390:]]}

    @application.get("/history")
    async def history(symbol: Literal["AAPL"] = "AAPL",
                      timeframe: Literal["1m", "5m", "15m", "1h", "1d"] = "1m",
                      dataset: Literal["raw", "polling", "auto"] = "raw", as_of: Optional[datetime] = None):
        try:
            if dataset == "auto":
                if as_of is not None:
                    raise ValueError("Automatic live dataset selection does not accept an as-of clock; select raw or polling")
                return await application.state.live_feed.snapshot(timeframe)
            return await asyncio.to_thread(history_service.calculate, symbol=symbol, timeframe=timeframe,
                                           dataset=dataset, as_of=as_of)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except sqlite3.Error as error:
            raise HTTPException(status_code=503, detail="History database unavailable") from error

    replay_service = ReplayService(RawObservationRepository(database), history_service)

    @application.get("/replay/catalog")
    async def replay_catalog():
        try:
            return await asyncio.to_thread(replay_service.catalog)
        except sqlite3.Error as error:
            raise HTTPException(status_code=503, detail="Replay database unavailable") from error

    @application.get("/replay/frame")
    async def replay_frame(start: datetime, end: datetime, symbol: Literal["AAPL"] = "AAPL",
                           timeframe: Literal["1m", "5m", "15m", "1h", "1d"] = "1m",
                           cursor: int = Query(0, ge=0), high_water: Optional[int] = Query(None, ge=0),
                           advance: bool = True):
        try:
            return await asyncio.to_thread(replay_service.frame, symbol=symbol, start=start, end=end,
                                           timeframe=timeframe, cursor=cursor, high_water=high_water, advance=advance)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except sqlite3.Error as error:
            raise HTTPException(status_code=503, detail="Replay database unavailable") from error

    @application.websocket("/ws/candles")
    async def candle_socket(websocket: WebSocket, symbol: Literal["AAPL"] = "AAPL",
                            timeframe: Literal["1m", "5m", "15m", "1h", "1d"] = "1m"):
        await websocket.accept()
        live = application.state.live_feed
        queue = live.subscribe()

        async def send_states():
            first = True
            while True:
                if not first:
                    try:
                        await asyncio.wait_for(queue.get(), timeout=5)
                    except asyncio.TimeoutError:
                        pass  # Recompute completion and status even without new prices.
                try:
                    message = await live.snapshot(timeframe)
                except (sqlite3.Error, ValueError):
                    await websocket.close(code=1011, reason="Candle calculation unavailable")
                    return
                message["type"] = "snapshot" if first else "candles"
                await websocket.send_json(message)
                first = False

        async def receive_disconnect():
            while True:
                await websocket.receive_text()

        sender, receiver = asyncio.create_task(send_states()), asyncio.create_task(receive_disconnect())
        try:
            done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
            for running in done:
                running.result()
        except (WebSocketDisconnect, OSError, asyncio.CancelledError):
            pass
        finally:
            live.clients.discard(queue)
            sender.cancel()
            receiver.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(sender, receiver, return_exceptions=True)

    @application.websocket("/ws/market")
    async def market_socket(websocket: WebSocket):
        await websocket.accept()
        feed = application.state.feed
        queue = feed.subscribe()

        async def send_updates():
            rows = await asyncio.to_thread(repository.current, "AAPL", SOURCE)
            await websocket.send_json({"type": "snapshot", "status": feed.status(),
                                       "observations": [row.model_dump(mode="json") for row in rows[-390:]]})
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    message = {"type": "status", "status": feed.status()}
                if message is None:
                    await websocket.close(code=1013, reason="Slow consumer; reconnect for snapshot")
                    return
                await websocket.send_json(message)

        async def receive_disconnect():
            while True:
                await websocket.receive_text()

        sender = asyncio.create_task(send_updates())
        receiver = asyncio.create_task(receive_disconnect())
        try:
            done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, OSError, asyncio.CancelledError):
            pass
        finally:
            feed.clients.discard(queue)
            for task in (sender, receiver):
                task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(sender, receiver, return_exceptions=True)

    return application


app = create_app()
