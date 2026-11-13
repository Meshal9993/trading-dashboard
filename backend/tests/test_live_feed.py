"""Prompt 08 constructed network fixtures, isolated SQLite, no Yahoo requests."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.db.database import Database
from backend.app.db.observations import ObservationRepository
from backend.app.db.raw_observations import RawObservationRepository
from backend.app.main import create_app
from backend.app.schemas.observation import Observation
from backend.app.services.feed import MarketFeed
from backend.app.services.history import HistoryService
from backend.app.services.live_feed import LiveCandleFeed, YahooQuoteConnection
from backend.app.services.yahoo import SOURCE

NOW = datetime(2026, 11, 9, 15, 0, 55, tzinfo=timezone.utc)


def quote(seconds=5, price=100, volume=1004):
    event=NOW.replace(second=0)+timedelta(seconds=seconds)
    return {'id':'AAPL','time':str(int(event.timestamp()*1000)),'price':price,'day_volume':str(volume),'exchange':'NMS','test_label':'CONSTRUCTED PROMPT 08 DATA'}


def services(tmp_path):
    db=Database(tmp_path/'test.sqlite3'); db.initialize()
    raw=RawObservationRepository(db); poll=ObservationRepository(db)
    polling=MarketFeed(None,poll,clock=lambda:NOW)
    history=HistoryService(raw,poll)
    live=LiveCandleFeed(raw,history,polling,clock=lambda:NOW)
    return db,raw,poll,polling,live


def test_commits_before_broadcast_and_updates_forming_candle(tmp_path):
    async def run():
        db,raw,_,_,live=services(tmp_path)
        queue=live.subscribe(); live.connected=True
        await live.accept(quote(-1,99,1000))
        first=await live.accept(quote())
        assert queue.get_nowait() is True
        assert len(raw.arrivals())==2
        snapshot=await live.snapshot()
        assert snapshot['last_recorded_arrival']['arrival_sequence']==first.arrival_sequence
        assert snapshot['candles'][-1]['close']==100 and snapshot['candles'][-1]['volume']==4
        assert not snapshot['metadata'][-1]['is_complete']
        await live.accept(quote(20,103,1009))
        updated=await live.snapshot()
        assert len(updated['candles'])==2 and updated['candles'][-1]['close']==103
        assert updated['candles'][-1]['volume']==9
        assert updated['feed_status']['data_age_seconds']==35
        assert len(raw.arrivals())==3
    asyncio.run(run())


def test_duplicates_corrections_and_next_interval_are_not_double_counted(tmp_path):
    async def run():
        _,raw,_,_,live=services(tmp_path); live.connected=True
        await live.accept(quote(-1,99,1000))
        original=await live.accept(quote())
        await live.accept(quote())
        await live.accept(quote(price=102),correction_of=original.observation_id,correction_evidence='TEST explicit correction')
        result=await live.snapshot()
        assert result['candles'][-1]['volume']==4 and result['candles'][-1]['close']==102
        assert len(raw.arrivals())==4
        live.clock=lambda:NOW+timedelta(minutes=1)
        await live.accept(quote(65,104,1010))
        result=await live.snapshot()
        assert len(result['candles'])==3 and result['candles'][-1]['close']==104
        assert result['metadata'][0]['is_complete']
    asyncio.run(run())


def test_polling_fallback_and_recovery_keep_datasets_separate(tmp_path):
    async def run():
        _,_,poll,polling,live=services(tmp_path)
        candle=Observation(symbol='AAPL',source=SOURCE,observation_id='saved',event_time=NOW.replace(second=0),receipt_time=NOW,open=90,high=91,low=89,close=90,volume=10,revision=0,is_complete=False)
        poll.store(candle); polling.latest=candle; polling.state='available'
        live.state='unavailable'
        fallback=await live.snapshot('5m')
        assert fallback['dataset']=='polling' and fallback['feed_status']['fallback_active']
        assert fallback['feed_status']['upstream_state']=='unavailable'
        assert fallback['candles'][0]['close']==90
        live.connected=True
        await live.accept(quote())
        streamed=await live.snapshot('5m')
        assert streamed['dataset']=='raw' and streamed['candles'][0]['close']==100
        assert not streamed['feed_status']['fallback_active']
    asyncio.run(run())


class Connection:
    def __init__(self, messages):
        self.messages=list(messages); self.closed=False
    async def open(self):
        pass
    async def receive(self):
        if not self.messages:
            await asyncio.Future()
        value=self.messages.pop(0)
        if isinstance(value,Exception): raise value
        return value
    async def close(self):
        self.closed=True


def test_worker_reconnects_resubscribes_and_closes_on_shutdown(tmp_path):
    async def run():
        _,raw,_,_,live=services(tmp_path)
        first=Connection([quote(),ConnectionError('TEST upstream disconnected')])
        second=Connection([quote(20,103,1009)])
        connections=iter([first,second])
        live.connection_factory=lambda:next(connections)
        live.retry_seconds=0.01
        task=asyncio.create_task(live.run())
        for _ in range(100):
            if len(raw.arrivals())==2: break
            await asyncio.sleep(0.005)
        assert len(raw.arrivals())==2 and first.closed
        assert live.state=='available' and live.failures==0
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        assert second.closed and not live.connected and live.state=='stopped'
    asyncio.run(run())


def test_persistence_failure_does_not_publish_unstored_quote(tmp_path):
    async def run():
        _,_,_,_,live=services(tmp_path)
        def fail(*args,**kwargs): raise OSError('TEST storage failure')
        live.repository.record=fail
        with pytest.raises(OSError): await live.accept(quote())
        assert live.last_arrival is None and live.latest is None and live.sequence==0
    asyncio.run(run())


@pytest.mark.parametrize("timeframe", ["1m", "5m", "15m", "1h", "1d"])
def test_socket_timeframe_states_and_http_history_share_calculation(tmp_path, timeframe):
    app=create_app(tmp_path/'app.sqlite3',feed_enabled=False,stream_enabled=False,stream_clock=lambda:NOW)
    with TestClient(app) as client:
        live=app.state.live_feed
        with client.websocket_connect('/ws/candles?timeframe=1m') as one, client.websocket_connect(f'/ws/candles?timeframe={timeframe}') as five:
            assert one.receive_json()['timeframe']=='1m'
            assert five.receive_json()['timeframe']==timeframe
            client.portal.call(live.accept,quote())
            state1,state5=one.receive_json(),five.receive_json()
            assert state1['timeframe']=='1m' and state5['timeframe']==timeframe
            assert state1['last_recorded_arrival']['arrival_sequence']==1
            assert state5['candles']==client.get(f'/history?dataset=auto&timeframe={timeframe}').json()['candles']
            assert len(RawObservationRepository(app.state.database).arrivals())==1
        assert not live.clients


def test_production_yfinance_adapter_subscribes_and_decodes_pinned_protocol():
    async def run():
        connection=YahooQuoteConnection()
        class Transport:
            async def recv(self): return '{"message":"TEST encoded payload"}'
        class Client:
            _ws=Transport()
            async def __aenter__(self): return self
            async def subscribe(self,symbols): assert symbols==['AAPL']
            def _decode_message(self,payload):
                assert payload=='TEST encoded payload'; return quote()
            async def close(self): pass
        connection.client=Client()
        await connection.open()
        assert await connection.receive()==quote()
        await connection.close()
    asyncio.run(run())


def test_polling_instantaneous_tail_does_not_break_fallback(tmp_path):
    async def run():
        _,_,poll,polling,live=services(tmp_path)
        for name,event in [('minute',NOW.replace(second=0)),('instant',NOW)]:
            candle=Observation(symbol='AAPL',source=SOURCE,observation_id=name,event_time=event,receipt_time=NOW,open=90,high=91,low=89,close=90,volume=10,revision=0,is_complete=False)
            poll.store(candle)
        result=await live.snapshot()
        assert result['dataset']=='polling' and len(result['candles'])==1
        assert len(poll.current_as_of('AAPL',SOURCE,NOW))==2
    asyncio.run(run())
