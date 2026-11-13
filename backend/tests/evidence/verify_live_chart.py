"""Read-only trace of a genuine live WebSocket chart state back to committed SQLite."""
import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import sys

from websockets.asyncio.client import connect
from backend.app.db.database import Database
from backend.app.db.raw_observations import RawObservationRepository
from backend.app.db.observations import ObservationRepository
from backend.app.services.history import HistoryService
from backend.app.services.minute_candles import parse_time, stamp


async def run(database, output):
    deadline=asyncio.get_running_loop().time()+60
    async with connect('ws://127.0.0.1:5173/ws/candles?symbol=AAPL&timeframe=1m',max_size=4*1024*1024) as socket:
        while asyncio.get_running_loop().time()<deadline:
            message=json.loads(await asyncio.wait_for(socket.recv(),timeout=20))
            record=message.get('last_recorded_arrival')
            if message['dataset']!='raw' or not record or not message['candles']:
                continue
            timestamp=stamp(parse_time(record['source_time_utc']).replace(second=0,microsecond=0))
            matches=[row for row in message['candles'] if row['timestamp']==timestamp]
            if not matches or matches[0]['close']!=record['price']:
                continue
            with sqlite3.connect(database) as connection:
                connection.row_factory=sqlite3.Row
                row=dict(connection.execute('SELECT a.arrival_sequence,a.receipt_time_utc,o.* FROM observation_arrivals a JOIN raw_observations o USING(observation_id) WHERE a.arrival_sequence=?',(record['arrival_sequence'],)).fetchone())
            payload=json.loads(row['payload_json'])
            assert payload['price']==record['price'] and str(payload['time'])==str(int(parse_time(record['source_time_utc']).timestamp()*1000))
            db=Database(database)
            expected=HistoryService(RawObservationRepository(db),ObservationRepository(db)).calculate(as_of=parse_time(message['as_of']))
            assert expected['candles']==message['candles']
            assert expected['metadata']==message['metadata']
            result={'result':'PASS','kind':'GENUINE YAHOO WEBSOCKET QUOTE, NOT CONSTRUCTED DATA',
                    'captured_at_utc':datetime.now(timezone.utc).isoformat(),'database':str(database),
                    'checks':['Committed arrival row exists before message receipt','Stored raw price/source timestamp match message trace',
                              'Derived candle close equals traced price','Full candle and metadata state equals independent as-of SQLite calculation'],
                    'wire_message':message,'stored_row':row,'matching_candle':matches[0]}
            output.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({'result':'PASS','arrival_sequence':record['arrival_sequence'],'raw_price':payload['price'],'source_time':record['source_time_utc'],'candle':matches[0]},indent=2))
            return
    raise RuntimeError('No genuine matching quote/candle arrived; live visual verification remains pending')


if __name__=='__main__':
    asyncio.run(run(Path(sys.argv[1]),Path(sys.argv[2])))
