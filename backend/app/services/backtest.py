"""Deterministic SMA simulation over saved AAPL quote arrivals."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from heapq import heappop, heappush

from backend.app.db.raw_observations import SOURCE
from backend.app.services.exchange_calendar import regular_session
from backend.app.services.minute_candles import NEW_YORK, parse_time
from backend.app.services.paper import MICROS_PER_DOLLAR, SQLITE_MAX_INT, balances_after_fill
from backend.app.services.pnl import cost_basis_after_fill, values
from backend.app.services.sma import SmaCrossovers

MINUTE = timedelta(minutes=1)


def price_micros(price):
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    value = Decimal(str(price))
    if not value.is_finite() or not 0 < value <= Decimal(SQLITE_MAX_INT) / MICROS_PER_DOLLAR:
        return None
    return int((value * MICROS_PER_DOLLAR).to_integral_value(rounding=ROUND_HALF_UP)) or None


class CandleTape:
    """Keep quotes for unfinished intervals and freeze their closes at the deadline."""

    def __init__(self):
        self.minutes = []
        self.quotes = {}
        self.open_minutes = {}

    def accept(self, row, minute, source, close):
        sequence = row["arrival_sequence"]
        previous_id = row["supersedes_id"]
        tie_order = sequence
        if row["disposition"] == "revision":
            previous = self.quotes.pop(previous_id, None)
            if previous is None:
                return  # The correction's target was never available before its candle froze.
            old_minute, _, tie_order, _ = previous
            self.open_minutes[old_minute].remove(previous_id)
        if minute not in self.open_minutes:
            self.open_minutes[minute] = set()
            heappush(self.minutes, minute)
        observation_id = row["observation_id"]
        self.open_minutes[minute].add(observation_id)
        self.quotes[observation_id] = (minute, source, tie_order, close)

    def completed_at(self, clock):
        while self.minutes and self.minutes[0] + MINUTE <= clock:
            minute = heappop(self.minutes)
            points = [self.quotes.pop(quote_id) for quote_id in self.open_minutes.pop(minute)]
            if points:
                _, _, _, close = max(points, key=lambda point: (point[1], point[2]))
                yield minute, close


class StrategyRun:
    def __init__(self, initial_cash, periods):
        self.initial_cash = self.cash = initial_cash
        self.shares = 0
        self.basis = Fraction(0)
        self.mark = None
        self.mark_time = None
        self.pending = None
        self.signals = []
        self.orders = []
        self.fills = []
        self.equity_curve = []
        self.completed_candles = 0
        self.sma = SmaCrossovers(periods)

    def value_at(self, time, price):
        self.mark = price
        self.mark_time = time.isoformat()
        point = {"timestamp": self.mark_time, "equity_micros": self.cash + self.shares * price}
        if self.equity_curve and self.equity_curve[-1]["timestamp"] == self.mark_time:
            self.equity_curve[-1] = point
        else:
            self.equity_curve.append(point)

    def complete(self, minute, close):
        decision = minute + MINUTE
        self.completed_candles += 1
        self.value_at(decision, price_micros(close))
        short, long, crossover = self.sma.update(close)
        if crossover is None:
            return
        signal = {
            "side": crossover, "candle_time": minute.isoformat(), "decision_time": decision.isoformat(),
            "short": short, "long": long, "order_id": None,
        }
        self.signals.append(signal)
        if self.pending or (crossover == "up" and self.shares) or (crossover == "down" and not self.shares):
            return

        order = {
            "id": len(self.orders) + 1, "side": "buy" if crossover == "up" else "close",
            "quantity": 1 if crossover == "up" else self.shares, "status": "pending",
            "submitted_at": decision.isoformat(), "rejection_reason": None,
        }
        self.pending = decision, order
        self.orders.append(order)
        signal["order_id"] = order["id"]

    def observe(self, row, source, receipt, clock, price):
        if self.pending is None:
            return
        decision, order = self.pending
        if receipt <= decision or source < decision:
            return  # A same-boundary receipt or an older source quote is not a later execution price.
        self.pending = None
        quantity, side = order["quantity"], order["side"]
        try:
            cash, shares = balances_after_fill(self.cash, self.shares, side, quantity, price)
        except ValueError as error:
            order.update(status="rejected", rejection_reason=str(error))
            return
        self.cash, self.shares = cash, shares
        self.basis = cost_basis_after_fill(self.basis, side, quantity, price, shares)
        order["status"] = "filled"
        self.fills.append({
            "order_id": order["id"], "side": side, "quantity": quantity, "price_micros": price,
            "source_time": source.isoformat(), "receipt_time": receipt.isoformat(),
            "arrival_sequence": row["arrival_sequence"],
        })
        self.value_at(clock, price)

    def performance(self):
        return {
            "cash_micros": self.cash, "shares": self.shares,
            **values(self.cash, self.shares, self.basis, self.mark, initial_cash=self.initial_cash),
            "equity_micros": self.cash + self.shares * (self.mark or 0),
            "price_micros": self.mark, "price_time": self.mark_time,
        }


def run_backtest(request, arrivals):
    """Consume one immutable SQLite journal read, ordered by arrival sequence."""
    initial_cash = int(request.initial_cash * MICROS_PER_DOLLAR)
    start = datetime.combine(request.start_date, time.min, NEW_YORK).astimezone(timezone.utc)
    stop = datetime.combine(request.end_date + timedelta(days=1), time.min, NEW_YORK).astimezone(timezone.utc)
    strategy = StrategyRun(initial_cash, (request.short_period, request.long_period))
    tape = CandleTape()
    clock, as_of, newest_source = start, None, None

    for row in arrivals:
        if row["symbol"] != request.symbol or row["source"] != SOURCE:
            continue
        receipt = parse_time(row["receipt_time_utc"])
        if receipt < start:
            continue
        clock = max(clock, receipt)
        if clock >= stop:
            break
        as_of = clock
        # A minute closing at this instant is frozen before the incoming receipt.
        for minute, close in tape.completed_at(clock):
            strategy.complete(minute, close)

        if row["disposition"] == "duplicate":
            continue
        source = parse_time(row["source_time_utc"])
        close = row["source_fields"].get("price")
        price = price_micros(close)
        if price is None or not start <= source <= receipt:
            continue
        session = regular_session(source.astimezone(NEW_YORK).date())
        if not session or not session.open <= source < session.close:
            continue
        minute = source.replace(second=0, microsecond=0)
        if row["disposition"] == "inserted" and (newest_source is None or source >= newest_source):
            newest_source = source
            strategy.observe(row, source, receipt, clock, price)
        if minute + MINUTE > clock:
            tape.accept(row, minute, source, close)

    warnings = []
    if strategy.completed_candles == 0:
        warnings.append("No completed AAPL candles were available in this range.")
    elif strategy.completed_candles < request.long_period:
        warnings.append("The range ended before the long SMA finished warming up.")
    if strategy.pending:
        warnings.append("An order is still pending because the range ended before an eligible quote arrived.")
    if strategy.shares:
        warnings.append("The open position is marked at the last recorded valuation without a forced close.")
    return {
        "symbol": request.symbol, "start_date": request.start_date.isoformat(), "end_date": request.end_date.isoformat(),
        "short_period": request.short_period, "long_period": request.long_period, "initial_cash_micros": initial_cash,
        "completed_candles": strategy.completed_candles, "as_of": as_of.isoformat() if as_of else None,
        "signals": strategy.signals, "orders": strategy.orders, "fills": strategy.fills,
        "performance": strategy.performance(), "equity_curve": strategy.equity_curve, "warnings": warnings,
    }
