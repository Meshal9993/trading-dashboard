"""Simple moving averages and confirmed crossovers for a candle snapshot."""

from collections import deque
from fractions import Fraction


MAX_PERIOD = 2000


def sma_periods(short, long):
    """Interpret the optional pair of query parameters without rounding."""
    if short is None and long is None:
        return None
    if not all(value and value.isascii() and value.isdecimal() and len(value) <= 4
               for value in (short, long)):
        raise ValueError("Supply both SMA periods as positive whole numbers")
    short_period, long_period = int(short), int(long)
    if not 1 <= short_period < long_period <= MAX_PERIOD:
        raise ValueError(f"SMA periods must satisfy 1 <= short < long <= {MAX_PERIOD}")
    return short_period, long_period


class SmaCrossovers:
    """State shared by the chart's batch calculation and forward-only decisions."""

    def __init__(self, periods):
        self.short_period, self.long_period = periods
        self.short_window = deque()
        self.long_window = deque()
        self.short_total = Fraction(0)
        self.long_total = Fraction(0)
        self.previous_side = 0

    def update(self, close):
        value = Fraction(str(close))
        self.short_window.append(value)
        self.short_total += value
        if len(self.short_window) > self.short_period:
            self.short_total -= self.short_window.popleft()
        self.long_window.append(value)
        self.long_total += value
        if len(self.long_window) > self.long_period:
            self.long_total -= self.long_window.popleft()

        short_value = long_value = crossover = None
        if len(self.short_window) == self.short_period:
            short_value = float(self.short_total / self.short_period)
        if len(self.long_window) == self.long_period:
            long_value = float(self.long_total / self.long_period)
            difference = self.short_total * self.long_period - self.long_total * self.short_period
            if difference:
                side = 1 if difference > 0 else -1
                if self.previous_side and side != self.previous_side:
                    crossover = "up" if side > 0 else "down"
                self.previous_side = side
        return short_value, long_value, crossover


def with_sma(snapshot, periods):
    """Calculate from the ordered candles in an already-resolved snapshot."""
    if periods is None:
        return snapshot
    completed = {row["timestamp"] for row in snapshot["metadata"] if row["is_complete"]}
    calculator = SmaCrossovers(periods)
    points = []
    for candle in snapshot["candles"]:
        timestamp = candle["timestamp"]
        short_value, long_value, crossover = (calculator.update(candle["close"]) if timestamp in completed
                                               else (None, None, None))
        points.append({"timestamp": timestamp, "short": short_value,
                       "long": long_value, "crossover": crossover})
    short_period, long_period = periods
    return dict(snapshot, sma={"short_period": short_period, "long_period": long_period, "points": points})
