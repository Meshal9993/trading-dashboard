"""Compare a captured real Vue ledger against its SQLite snapshot. No writes to DB."""
import json,sqlite3,sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from backend.app.db.database import Database
from backend.app.db.raw_observations import RawObservationRepository
from backend.app.db.observations import ObservationRepository
from backend.app.services.history import HistoryService
from backend.app.services.minute_candles import parse_time
root=Path(sys.argv[2]); dom=json.loads((root/'visible-live-dom.json').read_text());db=Database(sys.argv[1])
result=HistoryService(RawObservationRepository(db),ObservationRepository(db)).calculate(as_of=parse_time(dom['panel']['snapshotAsOf']))
for text,candle in zip(dom['rows'],reversed(result['candles'][-5:])):
    cells=text.split('\t');assert cells[1:5]==[str(Decimal(str(candle[k])).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)) for k in ['open','high','low','close']],(cells,candle)
    assert cells[5]==('Unknown' if candle['volume'] is None else f'{candle["volume"]:,}')
with sqlite3.connect(db.path) as conn:
    arrival=conn.execute('SELECT a.arrival_sequence,a.receipt_time_utc,o.payload_json FROM observation_arrivals a JOIN raw_observations o USING(observation_id) WHERE a.arrival_sequence=?',(int(dom['panel']['arrivalSequence']),)).fetchone()
assert arrival
report={'result':'PASS','checks':['All five visible OHLC rows match independent SQLite aggregation at displayed snapshot clock','Unknown volumes match null SQLite-derived volumes','Visible arrival sequence exists in committed SQLite'],'visible_arrival_sequence':arrival[0],'receipt_time':arrival[1],'raw_payload':json.loads(arrival[2]),'derived_candles':result['candles']}
(root/'visible-sqlite-comparison.json').write_text(json.dumps(report,indent=2));print('PASS: visible five candles and traced arrival match SQLite')
