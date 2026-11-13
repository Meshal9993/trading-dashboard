"""Verify two offline genuine-session replays via HTTP and independent SQL checks."""
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
import httpx

DB=Path(sys.argv[1]); OUT=Path(sys.argv[2]); START='2026-11-10T20:50:00Z'; END='2026-11-10T20:50:59.999999Z'

def records():
    with sqlite3.connect(f'file:{DB}?mode=ro',uri=True) as conn:
        return {name:conn.execute(f'SELECT * FROM {name} ORDER BY 1').fetchall() for name in
                ['raw_observations','observation_arrivals','market_observations']}

def digest(data): return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()

before=records()
with httpx.Client(base_url='http://127.0.0.1:5173',timeout=30) as client:
    assert client.get('/feed/status').json()['state']=='disabled'
    def run():
        cursor=0;high=None;frames=[]
        while True:
            query={'symbol':'AAPL','start':START,'end':END,'cursor':cursor}
            if high is not None: query['high_water']=high
            response=client.get('/replay/frame',params=query);response.raise_for_status()
            frame=response.json();frames.append(frame)
            cursor=frame['replay']['cursor'];high=frame['replay']['high_water']
            if frame['replay']['complete']: return frames
    first=run();second=run()
assert first==second and records()==before
assert first[-1]['replay']['total']>0
with sqlite3.connect(f'file:{DB}?mode=ro',uri=True) as conn:
    conn.row_factory=sqlite3.Row
    rows=[dict(row) for row in conn.execute('SELECT a.arrival_sequence,a.receipt_time_utc,a.disposition,o.* FROM observation_arrivals a JOIN raw_observations o USING(observation_id) WHERE a.arrival_sequence<=? ORDER BY a.arrival_sequence',(first[-1]['replay']['cursor'],))]
# Independent expected price calculation for the final displayed minute, excluding duplicates.
active={}
for row in rows:
    if row['disposition']=='duplicate':continue
    if row['supersedes_id'] is not None:active.pop(row['supersedes_id'],None)
    active[row['observation_id']]=row
points=sorted([r for r in active.values() if r['symbol']=='AAPL' and r['source']=='yahoo_finance_websocket'],key=lambda r:(r['source_time_ms'],r['arrival_sequence']))
minute=[json.loads(r['payload_json']) for r in points if r['source_time_utc'].startswith('2026-11-10T20:50:')]
expected={'open':minute[0]['price'],'high':max(p['price'] for p in minute),'low':min(p['price'] for p in minute),'close':minute[-1]['price']}
actual=next(c for c in first[-1]['candles'] if c['timestamp']=='2026-11-10T20:50:00Z')
assert all(actual[k]==v for k,v in expected.items())
# Real captured counters can regress: demonstrate the reason volume must be unknown.
minute_points=[(r,json.loads(r['payload_json'])) for r in points if r['source_time_utc'].startswith('2026-11-10T20:50:')]
regressions=[{'source_time':r['source_time_utc'],'previous':int(prev['day_volume']),'current':int(p['day_volume'])}
             for (_,prev),(r,p) in zip(minute_points,minute_points[1:]) if int(p['day_volume'])<int(prev['day_volume'])]
ambiguous=[{'source_time':r['source_time_utc'],'first_counter':int(prev['day_volume']),'next_counter':int(p['day_volume'])}
           for (previous_row,prev),(r,p) in zip(minute_points,minute_points[1:])
           if previous_row['source_time_ms']==r['source_time_ms'] and prev['day_volume']!=p['day_volume']]
assert ambiguous and actual['volume'] is None
result={'result':'PASS','kind':'GENUINE SAVED YAHOO SESSION; BOTH UPSTREAMS DISABLED',
        'selection':{'start':START,'end':END},'frames':len(first),'runs_identical':True,
        'counts_before':{k:len(v) for k,v in before.items()},'counts_after':{k:len(v) for k,v in records().items()},
        'records_sha256_before':digest(before),'records_sha256_after':digest(records()),
        'expected_ohlc_from_sql':expected,'expected_volume':None,'volume_reason':'Different cumulative counters share a source timestamp; independent messages cannot be assumed corrections',
        'counter_regressions':regressions,'ambiguous_same_timestamp_counters':ambiguous,'final_frame':first[-1], 'frame_hashes':[digest(f) for f in first]}
OUT.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ['final_frame','frame_hashes','counter_regressions']},indent=2))
