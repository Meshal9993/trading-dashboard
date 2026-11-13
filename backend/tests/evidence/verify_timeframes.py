"""Prompt 07A disposable-database and CSV verification; constructed TEST DATA only."""
from datetime import date
import importlib.metadata
import json
from pathlib import Path
import sys
import tempfile

from fastapi.testclient import TestClient
from backend.app.db.raw_observations import RawObservationRepository
from backend.app.main import create_app
from backend.app.services.exchange_calendar import regular_session
from backend.app.services.history import aggregate_minutes
from backend.app.services.minute_candles import stamp
from backend.tests.test_history import EXPECTED, base, minute, sample_minutes, seed_raw


def run(output):
    folder=Path(tempfile.mkdtemp(prefix='prompt07a-test-'))
    five=aggregate_minutes(base(),timeframe='5m')
    daily=aggregate_minutes(base(),timeframe='1d')
    assert five['candles']==[EXPECTED] and daily['candles']==[EXPECTED]
    assert five['metadata'][0]['available_minutes']==3 and five['metadata'][0]['expected_minutes']==5
    assert daily['metadata'][0]['expected_minutes']==390
    boundary=base(sample_minutes()+[minute('2026-11-09T14:35:00Z'),minute('2026-11-10T14:30:00Z')],as_of='2026-11-10T21:00:00Z')
    boundary5=aggregate_minutes(boundary,timeframe='5m')
    boundary_day=aggregate_minutes(boundary,timeframe='1d')
    assert len(boundary5['candles'])==3 and len(boundary_day['candles'])==2
    sessions={}
    for day in ['2026-03-06','2026-03-09','2026-10-30','2026-11-02','2026-11-26','2026-11-27','2026-12-24']:
        session=regular_session(date.fromisoformat(day))
        sessions[day]=None if session is None else {'open':stamp(session.open),'close':stamp(session.close)}
    assert sessions['2026-11-27']['close']=='2026-11-27T18:00:00Z'
    assert sessions['2026-03-09']['open']=='2026-03-09T13:30:00Z'
    early=aggregate_minutes(base([minute('2026-11-27T17:59:00Z')],as_of='2026-11-27T18:00:00Z'),timeframe='1h')
    assert early['metadata'][0]['expected_minutes']==30
    unknown=aggregate_minutes(base([minute('2026-11-09T14:30:00Z',None)]),timeframe='5m')
    zero=aggregate_minutes(base([minute('2026-11-09T14:30:00Z',0)]),timeframe='5m')
    assert unknown['candles'][0]['volume'] is None and zero['candles'][0]['volume']==0
    with TestClient(create_app(folder/'raw.sqlite3',feed_enabled=False)) as client:
        repo=RawObservationRepository(client.app.state.database)
        seed_raw(repo)
        before=repo.arrivals()
        api={}
        for interval in ['1m','5m','15m','1h','1d']:
            response=client.get('/history',params={'timeframe':interval,'as_of':'2026-11-09T21:00:00Z'})
            assert response.status_code==200
            api[interval]=response.json()
        assert api['5m']['candles']==[EXPECTED]
        assert api['1d']['candles']==[EXPECTED]
        assert repo.arrivals()==before
        assert client.get('/observations').status_code==200
        assert client.get('/health').status_code==200
    result={'result':'PASS','input_kind':'CONSTRUCTED INSTRUCTOR CSV AND TEST OBSERVATIONS; NOT LIVE YAHOO PRICES',
            'database_path':str(folder/'raw.sqlite3'),'calendar_package':importlib.metadata.version('pandas_market_calendars'),
            'expected':EXPECTED,'five_minute':five,'daily':daily,'boundary_five_minute':boundary5,
            'boundary_daily':boundary_day,'session_checks':sessions,'early_close_final_hour':early,
            'unknown_volume':unknown,'known_zero_volume':zero,'history_api':api,'journal_unchanged':True,
            'sources':['https://nasdaqtrader.com/Trader.aspx?id=Calendar','https://pandas-market-calendars.readthedocs.io/en/latest/usage.html']}
    output.mkdir(parents=True,exist_ok=True)
    (output/'instructor-example.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:value for key,value in result.items() if key not in ['history_api','boundary_five_minute','boundary_daily','unknown_volume','known_zero_volume']},indent=2))


if __name__=='__main__':
    run(Path(sys.argv[1]))
