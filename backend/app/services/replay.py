"""Stateless, read-only receipt-journal replay. No provider calls or replay writes."""
from backend.app.db.raw_observations import SOURCE
from backend.app.services.minute_candles import aware_utc, parse_time


class ReplayService:
    def __init__(self, repository, history):
        self.repository, self.history = repository, history

    def catalog(self):
        rows = [r for r in self.repository.arrivals() if r['source'] == SOURCE and r['symbol'] == 'AAPL']
        return {'range_basis': 'backend_receipt_time_utc_inclusive', 'symbols': [
            {'symbol': symbol, 'first_receipt': min(r['receipt_time_utc'] for r in rows),
             'last_receipt': max(r['receipt_time_utc'] for r in rows), 'arrival_count': len(rows)}
            for symbol in ['AAPL'] if rows]}

    def frame(self, *, symbol, start, end, timeframe='1m', cursor=0, high_water=None, advance=True):
        start, end = aware_utc(start), aware_utc(end)
        if start > end:
            raise ValueError('Replay start must not be after end')
        if symbol != 'AAPL':
            raise ValueError('Only saved AAPL regular-session observations are supported')
        rows = self.repository.arrivals()
        upper = max((r['arrival_sequence'] for r in rows), default=0)
        high_water = upper if high_water is None else high_water
        if high_water < 0 or high_water > upper or cursor < 0 or cursor > high_water:
            raise ValueError('Invalid replay boundary')
        selected = [r for r in rows if r['arrival_sequence'] <= high_water and r['source'] == SOURCE
                    and r['symbol'] == symbol and start <= parse_time(r['receipt_time_utc']) <= end]
        ids = [r['arrival_sequence'] for r in selected]
        if cursor and cursor not in ids:
            raise ValueError('Replay cursor does not belong to this selection')
        remaining = [r for r in selected if r['arrival_sequence'] > cursor]
        current = remaining[0]['arrival_sequence'] if advance and remaining else cursor
        seen = [r for r in selected if r['arrival_sequence'] <= current]
        prefix = [r for r in rows if r['arrival_sequence'] <= current]
        # A backward wall-clock adjustment must not hide an already arrived record.
        # The calculation clock is monotonic; the exact original receipt is separate.
        clock = max((parse_time(r['receipt_time_utc']) for r in prefix), default=start)
        result = self.history.calculate(symbol=symbol, timeframe=timeframe, as_of=clock,
                                        dataset='raw', arrivals=prefix)
        # Earlier journal context is needed for correction chains and cumulative-volume
        # baselines. Show only intervals touched by selected arrivals revealed so far.
        touched = {parse_time(r['source_time_utc']).replace(second=0, microsecond=0) for r in seen}
        visible = {m['timestamp'] for m in result['metadata']
                   if any(parse_time(m['timestamp']) <= t < parse_time(m['interval_end']) for t in touched)}
        result['candles'] = [c for c in result['candles'] if c['timestamp'] in visible]
        result['metadata'] = [m for m in result['metadata'] if m['timestamp'] in visible]
        last = seen[-1] if seen else None
        trace = ({key: last[key] for key in ('arrival_sequence', 'observation_id', 'receipt_time_utc',
                  'source_time_utc', 'disposition', 'revision', 'supersedes_id')} if last else None)
        return dict(result, mode='replay', type='snapshot', sequence=current,
                    replay={'cursor': current, 'high_water': high_water, 'processed': len(seen),
                            'total': len(selected), 'complete': not any(i > current for i in ids),
                            'empty': not selected, 'clock': clock.isoformat(), 'arrival': trace,
                            'range_basis': 'backend_receipt_time_utc_inclusive',
                            'message': 'No recorded observations in this selection.' if not selected else None},
                    last_recorded_arrival=trace,
                    feed_status={'state': 'replay', 'source': SOURCE, 'fallback_active': False,
                                 'upstream_state': 'not_required', 'upstream_connected': False,
                                 'latest_event_time': last['source_time_utc'] if last else None,
                                 'latest_receipt_time': last['receipt_time_utc'] if last else None,
                                 'is_realtime': False, 'data_label': 'Replay · recorded Yahoo observations'})
