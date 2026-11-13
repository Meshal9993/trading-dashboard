"""Constructed replay fixtures only; no Yahoo calls, disposable SQLite."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.db.raw_observations import RawObservationRepository

BASE = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


def record(repo, event, receipt, price, volume, **kwargs):
    return repo.record({'id':'AAPL','time':str(int((BASE+timedelta(seconds=event)).timestamp()*1000)),
                        'price':price,'day_volume':str(volume),'exchange':'NMS',
                        'fixture':'CONSTRUCTED PROMPT 09 TEST'},BASE+timedelta(seconds=receipt),**kwargs)


def populate(repo):
    record(repo,-1,0,99,1000)
    first=record(repo,5,10,100,1004)
    record(repo,40,41,101,1011)
    middle=record(repo,20,41,103,1009) # Same receipt clock, later durable sequence.
    record(repo,20,42,104,1009,correction_of=middle.observation_id,correction_evidence='TEST identifiable correction')
    record(repo,5,43,100,1004) # Exact duplicate is a replay step, not extra volume.
    record(repo,61,62,102,1015)
    return first


def params(**extra):
    return {'symbol':'AAPL','start':(BASE+timedelta(seconds=10)).isoformat(),
            'end':(BASE+timedelta(seconds=65)).isoformat(),**extra}


def test_replay_late_revision_duplicate_prefix_and_repeat_are_read_only(tmp_path):
    app=create_app(tmp_path/'replay.sqlite3',feed_enabled=False,stream_enabled=False)
    with TestClient(app) as client:
        repo=RawObservationRepository(app.state.database); populate(repo)
        before=json.dumps(repo.arrivals(),sort_keys=True)
        catalog=client.get('/replay/catalog').json()
        assert catalog['symbols'][0]['arrival_count']==7
        def run():
            frames=[];cursor=0;high=None
            while True:
                query=params(cursor=cursor)
                if high is not None: query['high_water']=high
                response=client.get('/replay/frame',params=query);assert response.status_code==200,response.text
                frame=response.json();frames.append(frame)
                cursor=frame['replay']['cursor'];high=frame['replay']['high_water']
                if frame['replay']['complete']: return frames
        frames=run()
        assert [f['replay']['cursor'] for f in frames]==[2,3,4,5,6,7]
        assert frames[0]['candles'][0]['close']==100 and frames[0]['candles'][0]['high']==100
        assert frames[1]['candles'][0]['high']==101 # Middle not revealed by equal receipt clock.
        assert frames[2]['candles'][0]=={'timestamp':'2026-11-09T15:00:00Z','open':100,'high':103,'low':100,'close':101,'volume':11}
        assert not frames[2]['metadata'][0]['is_complete']
        assert frames[3]['candles'][0]['high']==104
        assert frames[4]['candles']==frames[3]['candles']
        assert frames[4]['replay']['arrival']['disposition']=='duplicate'
        assert frames[-1]['metadata'][0]['is_complete']
        assert run()==frames
        assert json.dumps(repo.arrivals(),sort_keys=True)==before
        with app.state.database.connect() as conn:
            assert conn.execute('SELECT count(*) FROM raw_observations').fetchone()[0]==6
            assert conn.execute('SELECT count(*) FROM observation_arrivals').fetchone()[0]==7


def test_replay_freezes_boundary_and_recalculates_timeframe_without_advancing(tmp_path):
    app=create_app(tmp_path/'replay.sqlite3',feed_enabled=False,stream_enabled=False)
    with TestClient(app) as client:
        repo=RawObservationRepository(app.state.database);populate(repo)
        frame=client.get('/replay/frame',params=params()).json()
        record(repo,25,50,999,1020)
        same=client.get('/replay/frame',params=params(cursor=2,high_water=7,advance=False,timeframe='5m')).json()
        assert same['replay']['processed']==1 and same['replay']['total']==6
        assert same['timeframe']=='5m' and same['candles'][0]['high']==100
        assert same['replay']['cursor']==2


def test_replay_empty_and_invalid_selections(tmp_path):
    app=create_app(tmp_path/'replay.sqlite3',feed_enabled=False,stream_enabled=False)
    with TestClient(app) as client:
        assert client.get('/replay/catalog').json()['symbols']==[]
        empty=client.get('/replay/frame',params=params()).json()
        assert empty['replay']['empty'] and empty['candles']==[]
        assert 'No recorded observations' in empty['replay']['message']
        for patch in [{'end':BASE.isoformat()},{'start':'2026-11-09T15:00:00'}, {'cursor':-1},{'high_water':999},{'symbol':'MSFT'},{'timeframe':'2m'}]:
            assert client.get('/replay/frame',params=params(**patch)).status_code==422


def test_replay_clock_rollback_preserves_arrival_order(tmp_path):
    app=create_app(tmp_path/'replay.sqlite3',feed_enabled=False,stream_enabled=False)
    with TestClient(app) as client:
        repo=RawObservationRepository(app.state.database)
        record(repo,5,20,100,1000);record(repo,10,15,101,1001)
        frame=client.get('/replay/frame',params=params(cursor=1)).json()
        assert frame['replay']['cursor']==2 and frame['candles'][0]['close']==101
        assert frame['replay']['clock']==(BASE+timedelta(seconds=20)).isoformat()
        assert datetime.fromisoformat(frame['replay']['arrival']['receipt_time_utc'])==BASE+timedelta(seconds=15)
