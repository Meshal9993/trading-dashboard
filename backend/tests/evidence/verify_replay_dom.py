"""Compare two actual visible replay captures against independently SQL-checked state."""
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
root=Path(sys.argv[1])
verified=json.loads((root/'real-replay-comparison.json').read_text())
first=json.loads((root/'replay-run1-dom.json').read_text())
second=json.loads((root/'replay-run2-dom.json').read_text())
frame=verified['final_frame']
assert first['rows']==second['rows']
assert first['panel']==second['panel']
assert first['panel']['snapshotAsOf']==frame['as_of']
assert int(first['panel']['arrivalSequence'])==frame['replay']['cursor']
assert '69 / 69 arrivals · Finished' in first['text']
assert 'REPLAY · RECORDED DATA' in first['text'] and 'Not required for replay' in first['text']
for text,candle in zip(first['rows'],reversed(frame['candles'][-5:])):
    cells=text.split('\t')
    expected=[str(Decimal(str(candle[k])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)) for k in ['open','high','low','close']]
    assert cells[1:5]==expected
    assert cells[5]==('Unknown' if candle['volume'] is None else f'{candle["volume"]:,}')
result={'result':'PASS','checks':['Two visible replay runs have identical rows, cursor and clock',
        'Visible OHLCV matches the SQL-verified final API frame', 'Replay provenance and disconnected-upstream independence are visible'],
        'cursor':frame['replay']['cursor'],'clock':frame['as_of'],'visible_rows':first['rows']}
(root/'visible-replay-comparison.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
