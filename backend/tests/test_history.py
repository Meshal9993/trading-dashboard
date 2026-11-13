"""Prompt 07A official CSV and constructed boundary/correction fixtures (TEST DATA)."""
import csv
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.db.raw_observations import RawObservationRepository
from backend.app.db.observations import ObservationRepository
from backend.app.main import create_app
from backend.app.schemas.observation import Observation
from backend.app.services.exchange_calendar import regular_session
from backend.app.services.history import aggregate_minutes
from backend.app.services.minute_candles import parse_time, stamp
from backend.app.services.yahoo import SOURCE

CSV = Path(__file__).resolve().parents[2] / 'live_market_workstation/prompts/data/sample-candles.csv'
EXPECTED = {"timestamp": "2026-11-09T14:30:00Z", "open": 100, "high": 105, "low": 99, "close": 104, "volume": 3550}


def sample_minutes():
    with CSV.open() as handle:
        return [dict(timestamp=row['timestamp'], **{key: float(row[key]) for key in ('open','high','low','close')}, volume=int(row['volume'])) for row in csv.DictReader(handle)]


def minute(timestamp, volume=100):
    return dict(timestamp=timestamp, open=100, high=103, low=99, close=102, volume=volume)


def base(rows=None, as_of='2026-11-09T21:00:00Z'):
    rows = sample_minutes() if rows is None else rows
    return {'symbol': 'AAPL', 'source': 'CONSTRUCTED_TEST_MINUTES', 'as_of': as_of, 'candles': rows,
            'metadata': [{'timestamp': row['timestamp'], 'is_complete': True,
                          'available_at': (parse_time(row['timestamp'])+timedelta(minutes=1)).isoformat()} for row in rows]}


def test_exact_official_five_minute_example_and_partial_day():
    five = aggregate_minutes(base(), timeframe='5m')
    assert five['candles'] == [EXPECTED]
    status = five['metadata'][0]
    assert status['available_minutes'] == 3 and status['expected_minutes'] == 5
    assert status['is_complete'] and not status['is_coverage_complete']
    assert status['coverage_status'] == 'partial' and status['missing_minutes'] == 2
    assert set(five['candles'][0]) == {'timestamp','open','high','low','close','volume'}
    daily = aggregate_minutes(base(), timeframe='1d')
    assert daily['candles'] == [EXPECTED]
    assert daily['metadata'][0]['expected_minutes'] == 390
    assert not daily['metadata'][0]['is_coverage_complete']


def test_boundary_minute_and_next_session():
    data = base(sample_minutes()+[minute('2026-11-09T14:35:00Z'), minute('2026-11-10T14:30:00Z')], as_of='2026-11-10T21:00:00Z')
    five = aggregate_minutes(data, timeframe='5m')
    assert [row['timestamp'] for row in five['candles']] == ['2026-11-09T14:30:00Z','2026-11-09T14:35:00Z','2026-11-10T14:30:00Z']
    day = aggregate_minutes(data, timeframe='1d')
    assert len(day['candles']) == 2
    assert day['metadata'][0]['available_minutes'] == 4
    assert day['metadata'][1]['available_minutes'] == 1


@pytest.mark.parametrize('timeframe,expected', [('1m',3),('5m',1),('15m',1),('1h',1),('1d',1)])
def test_all_timeframes(timeframe, expected):
    assert len(aggregate_minutes(base(),timeframe=timeframe)['candles']) == expected


def test_gap_does_not_split_daily_session_or_fabricate_missing_minutes():
    data = base([minute('2026-11-09T14:30:00Z'),minute('2026-11-09T20:59:00Z')])
    result = aggregate_minutes(data, timeframe='1d')
    assert len(result['candles']) == 1 and result['candles'][0]['volume'] == 200
    assert result['metadata'][0]['available_minutes'] == 2
    assert result['metadata'][0]['missing_minutes'] == 388


def test_unknown_and_zero_are_distinct_from_partial_coverage():
    unknown = aggregate_minutes(base([minute('2026-11-09T14:30:00Z',None)]),timeframe='5m')
    zero = aggregate_minutes(base([minute('2026-11-09T14:30:00Z',0)]),timeframe='5m')
    assert unknown['candles'][0]['volume'] is None and unknown['metadata'][0]['volume_status']=='unknown'
    assert zero['candles'][0]['volume'] == 0 and zero['metadata'][0]['volume_status']=='known'
    assert not zero['metadata'][0]['is_coverage_complete']


def test_full_coverage_and_clock_completion_are_independent():
    data = base([minute(f'2026-11-09T14:3{i}:00Z') for i in range(5)],as_of='2026-11-09T14:35:00Z')
    full = aggregate_minutes(data,timeframe='5m')
    assert full['metadata'][0]['is_coverage_complete'] and full['metadata'][0]['is_complete']
    forming = base([minute('2026-11-09T14:30:00Z')],as_of='2026-11-09T14:30:30Z')
    forming['metadata'][0] = {'timestamp':'2026-11-09T14:30:00Z','is_complete':False,'available_at':'2026-11-09T14:30:05Z'}
    result = aggregate_minutes(forming)
    assert result['metadata'][0]['available_minutes'] == 1
    assert not result['metadata'][0]['is_complete'] and not result['metadata'][0]['is_coverage_complete']


@pytest.mark.parametrize('day,opening,closing', [
    ('2026-03-06','14:30','21:00'),('2026-03-09','13:30','20:00'),
    ('2026-10-30','13:30','20:00'),('2026-11-02','14:30','21:00'),
    ('2026-11-27','14:30','18:00'),('2026-12-24','14:30','18:00')])
def test_calendar_dst_and_early_closes(day, opening, closing):
    session = regular_session(datetime.fromisoformat(day).date())
    assert stamp(session.open) == f'{day}T{opening}:00Z'
    assert stamp(session.close) == f'{day}T{closing}:00Z'


def test_hourly_buckets_anchor_to_session_not_wall_clock():
    data = base([minute('2026-03-09T14:10:00Z')],as_of='2026-03-09T20:00:00Z')
    result = aggregate_minutes(data,timeframe='1h')
    assert result['candles'][0]['timestamp']=='2026-03-09T13:30:00Z'
    assert result['metadata'][0]['interval_end']=='2026-03-09T14:30:00Z'


def test_early_close_final_hour_is_short_and_daily_coverage_is_210():
    data = base([minute('2026-11-27T17:59:00Z'),minute('2026-11-27T18:00:00Z')],as_of='2026-11-27T18:01:00Z')
    hour = aggregate_minutes(data,timeframe='1h')
    assert len(hour['candles'])==1 and hour['candles'][0]['timestamp']=='2026-11-27T17:30:00Z'
    assert hour['metadata'][0]['expected_minutes']==30 and hour['metadata'][0]['is_complete']
    assert hour['metadata'][0]['interval_end']=='2026-11-27T18:00:00Z'
    assert hour['excluded_nonregular_minutes']==1
    assert aggregate_minutes(data,timeframe='1d')['metadata'][0]['expected_minutes']==210


@pytest.mark.parametrize('day',['2026-01-01','2026-01-19','2026-02-16','2026-04-03','2026-05-25','2026-06-19','2026-07-03','2026-09-07','2026-11-26','2026-12-25','2026-11-07'])
def test_official_2026_holidays_and_weekends_excluded(day):
    assert regular_session(datetime.fromisoformat(day).date()) is None
    result = aggregate_minutes(base([minute(f'{day}T14:30:00Z')],as_of=f'{day}T22:00:00Z'),timeframe='1d')
    assert result['candles']==[] and result['excluded_nonregular_minutes']==1


def test_premarket_and_close_boundary_excluded():
    data = base([minute('2026-11-09T14:29:00Z'),minute('2026-11-09T14:30:00Z'),minute('2026-11-09T21:00:00Z')],as_of='2026-11-09T22:00:00Z')
    result=aggregate_minutes(data,timeframe='1d')
    assert result['metadata'][0]['available_minutes']==1 and result['excluded_nonregular_minutes']==2


def test_no_future_minute_or_receipt_and_no_future_snapshot():
    data = base(as_of='2026-11-09T14:32:30Z')
    result = aggregate_minutes(data,timeframe='5m')
    assert result['metadata'][0]['available_minutes']==2  # CSV minute 14:32 available at 14:33.
    assert result['candles'][0]['close']==101
    assert not result['metadata'][0]['is_complete']
    with pytest.raises(ValueError,match='future base snapshot'):
        aggregate_minutes(base(),as_of=parse_time('2026-11-09T14:32:30Z'))


def seed_raw(repo, late_last=False):
    baseline=1000
    start=parse_time('2026-11-09T14:30:00Z')
    repo.record({'id':'AAPL','price':99,'time':str(int((start-timedelta(seconds=1)).timestamp()*1000)), 'day_volume':'1000','exchange':'NMS','test_label':'CONSTRUCTED PROMPT 07A DATA'},start-timedelta(milliseconds=500))
    references=[]
    for index,candle in enumerate(sample_minutes()):
        parts=[candle['volume']//4]*3+[candle['volume']-3*(candle['volume']//4)]
        for offset,key,delta in zip([5,15,25,50],['open','high','low','close'],parts):
            event=start+timedelta(minutes=index,seconds=offset); baseline+=delta
            item={'id':'AAPL','price':candle[key],'time':str(int(event.timestamp()*1000)), 'day_volume':str(baseline),'exchange':'NMS','test_label':'CONSTRUCTED PROMPT 07A DATA'}
            receipt=event+timedelta(milliseconds=1)
            if late_last and index==2:
                receipt=parse_time('2026-11-09T14:40:00Z')+timedelta(seconds=offset)
            references.append((repo.record(item,receipt),item))
    return references


def test_history_endpoint_uses_saved_raw_minutes_and_defaults_to_one_minute(tmp_path):
    with TestClient(create_app(tmp_path/'raw.sqlite3',feed_enabled=False)) as client:
        seed_raw(RawObservationRepository(client.app.state.database))
        response=client.get('/history',params={'timeframe':'5m','as_of':'2026-11-09T14:35:00Z'})
        assert response.status_code==200
        assert response.json()['candles']==[EXPECTED]
        assert response.json()['metadata'][0]['available_minutes']==3
        assert len(client.get('/history').json()['candles'])==3
        assert 'status' in client.get('/observations').json()  # Existing contract intact.
        assert client.get('/health').status_code==200


def test_late_observation_and_identifiable_revision_only_appear_after_receipt(tmp_path):
    with TestClient(create_app(tmp_path/'late.sqlite3',feed_enabled=False)) as client:
        repo=RawObservationRepository(client.app.state.database)
        refs=seed_raw(repo,late_last=True)
        before=client.get('/history',params={'timeframe':'5m','as_of':'2026-11-09T14:35:00Z'}).json()
        assert before['metadata'][0]['available_minutes']==2
        after=client.get('/history',params={'timeframe':'5m','as_of':'2026-11-09T14:41:00Z'}).json()
        assert after['candles']==[EXPECTED]
        reference,payload=refs[1] # First minute high.
        repo.record(dict(payload,price=110),parse_time('2026-11-09T14:42:00Z'),correction_of=reference.observation_id,correction_evidence='CONSTRUCTED TEST explicit high correction')
        revised=client.get('/history',params={'timeframe':'5m','as_of':'2026-11-09T14:43:00Z'}).json()
        assert revised['candles']==[dict(EXPECTED,high=110)]
        assert client.get('/history',params={'timeframe':'5m','as_of':'2026-11-09T14:41:00Z'}).json()==after
        assert revised['metadata'][0]['available_minutes']==3


def test_polling_history_as_of_selects_received_revision_not_latest_view(tmp_path):
    with TestClient(create_app(tmp_path/'poll.sqlite3',feed_enabled=False)) as client:
        repo=ObservationRepository(client.app.state.database)
        original=Observation(symbol='AAPL',source=SOURCE,observation_id='one',event_time=parse_time('2026-11-09T14:30:00Z'),receipt_time=parse_time('2026-11-09T14:31:00Z'),open=100,high=103,low=99,close=102,volume=1200,revision=0,is_complete=True)
        repo.store(original)
        repo.store(original.model_copy(update={'revision':1,'high':104,'close':104,'receipt_time':parse_time('2026-11-09T15:00:00Z')}))
        before=client.get('/history',params={'dataset':'polling','timeframe':'5m','as_of':'2026-11-09T14:35:00Z'}).json()
        after=client.get('/history',params={'dataset':'polling','timeframe':'5m','as_of':'2026-11-09T15:01:00Z'}).json()
        assert before['candles'][0]['close']==102 and after['candles'][0]['close']==104
        assert len(after['candles'])==1 and after['source']==SOURCE
        assert client.get('/observations').json()['observations'][0]['revision']==1


@pytest.mark.parametrize('params',[{'timeframe':'2m'},{'symbol':'MSFT'},{'dataset':'invented'},{'as_of':'2026-11-09T15:00:00'}])
def test_history_rejects_invalid_or_naive_parameters(tmp_path,params):
    with TestClient(create_app(tmp_path/'empty.sqlite3',feed_enabled=False)) as client:
        assert client.get('/history',params=params).status_code==422


def test_history_empty_and_read_only(tmp_path):
    with TestClient(create_app(tmp_path/'empty.sqlite3',feed_enabled=False)) as client:
        assert client.get('/history').json()['candles']==[]
        repo=RawObservationRepository(client.app.state.database)
        seed_raw(repo)
        before=repo.arrivals()
        for interval in ['1m','5m','15m','1h','1d']:
            assert client.get('/history',params={'timeframe':interval}).status_code==200
        assert before==repo.arrivals()


def test_duplicate_base_minutes_are_not_summed_as_revisions():
    data=base([minute('2026-11-09T14:30:00Z')]*2)
    with pytest.raises(ValueError,match='Resolve revised'):
        aggregate_minutes(data,timeframe='5m')


@pytest.mark.parametrize('timeframe,boundary',[('1m','14:31'),('5m','14:35'),('15m','14:45'),('1h','15:30')])
def test_exact_intraday_boundary_starts_a_new_bucket(timeframe,boundary):
    data=base([minute('2026-11-09T14:30:00Z'),minute(f'2026-11-09T{boundary}:00Z')])
    result=aggregate_minutes(data,timeframe=timeframe)
    assert [row['timestamp'] for row in result['candles']]==['2026-11-09T14:30:00Z',f'2026-11-09T{boundary}:00Z']


def test_future_source_minute_is_excluded_even_if_receipt_metadata_is_early():
    data=base([minute('2026-11-09T14:40:00Z')],as_of='2026-11-09T14:35:00Z')
    data['metadata'][0]['available_at']='2026-11-09T14:34:00Z'
    assert aggregate_minutes(data,timeframe='5m')['candles']==[]
