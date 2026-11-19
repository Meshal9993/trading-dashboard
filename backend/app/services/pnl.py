"""Exact weighted-average accounting and immutable paper valuation snapshots."""
from datetime import timedelta
import logging
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

from backend.app.db.paper import PaperPnLRepository
from backend.app.db.raw_observations import RawObservationRepository
from backend.app.services.history import aggregate_minutes
from backend.app.services.minute_candles import form_one_minute_candles, parse_time

STARTING_CASH = 1_000_000_000
MICROS_PER_DOLLAR = 1_000_000
MAX_SEQUENCE = (1 << 63) - 1
VALID_PRICE_AGE = timedelta(seconds=180)
MINUTE = timedelta(minutes=1)
FILL_SOURCE = "yahoo_finance_websocket_quote"
CANDLE_SOURCE = "yahoo_finance_websocket_1m_close"
logger = logging.getLogger(__name__)


def iso_utc(time):
    return time.isoformat(timespec="microseconds")


def basis_of(row):
    return Fraction(row["cost_basis_exact"]) if row else Fraction(0)


def rounded_micros(value):
    """Round the exact rational to display micros, half away from zero."""
    return int(value + Fraction(1, 2)) if value >= 0 else -rounded_micros(-value)


def cost_basis_after_fill(basis, side, quantity, price, shares_after):
    if side == "buy":
        return basis + quantity * price
    return basis * Fraction(shares_after, shares_after + quantity)


def values(cash, shares, basis, price, *, initial_cash=STARTING_CASH):
    realized_exact = cash + basis - initial_cash
    unrealized_exact = None if shares and price is None else shares * (price or 0) - basis
    return {
        "average_cost_micros": rounded_micros(basis / shares) if shares else None,
        "realized_micros": rounded_micros(realized_exact),
        "unrealized_micros": rounded_micros(unrealized_exact) if unrealized_exact is not None else None,
        "total_micros": rounded_micros(realized_exact + unrealized_exact) if unrealized_exact is not None else None,
    }


class PaperPnL:
    def __init__(self, database):
        self.repository = PaperPnLRepository()
        self.raw = RawObservationRepository(database)

    def fill(self, connection, *, account_id, order_id, side, quantity, cash, shares, price, event_time, price_time):
        previous = self.repository.latest(connection, account_id)
        basis = cost_basis_after_fill(basis_of(previous), side, quantity, price, shares)
        self._record(connection, account_id=account_id, event_key=f"fill:{order_id}", event="fill",
                     event_time=event_time, order_id=order_id, candle_start=None, price_source=FILL_SOURCE,
                     price_time=price_time, price=price, cash=cash, shares=shares, basis=basis)

    def backfill(self, connection):
        """Recover the fill ledger from existing 09C accounts without re-executing trades."""
        for fill in self.repository.legacy_fills(connection):
            previous = self.repository.latest(connection, fill["account_id"])
            cash = previous["cash_micros"] if previous else STARTING_CASH
            shares = previous["shares"] if previous else 0
            change = fill["quantity"] if fill["side"] == "buy" else -fill["quantity"]
            self.fill(connection, account_id=fill["account_id"], order_id=fill["order_id"],
                      side=fill["side"], quantity=fill["quantity"], cash=cash - change * fill["price_micros"],
                      shares=shares + change, price=fill["price_micros"], event_time=fill["executed_at"],
                      price_time=fill["source_time_utc"])

    def completed(self, connection, account, clock, cursor=MAX_SEQUENCE):
        if not account["shares"]:
            return
        account_id = account["id"]
        last_fill = self.repository.latest(connection, account_id, "fill")
        if last_fill is None:
            return
        last_fill_receipt = parse_time(self.repository.fill_receipt(connection, last_fill["order_id"]))
        first = last_fill_receipt.replace(second=0, microsecond=0)
        prior_mark = self.repository.latest(connection, account_id, "completed_candle")
        if prior_mark:
            first = max(first, parse_time(prior_mark["price_time"]))
        stop = clock.replace(second=0, microsecond=0)
        if first >= stop or not self.repository.has_completed_price(
                connection, int(first.timestamp() * 1000), int(stop.timestamp() * 1000), iso_utc(clock), cursor):
            return
        try:
            arrivals = self.raw.arrivals(through_sequence=cursor, transaction=connection)
            snapshot = aggregate_minutes(form_one_minute_candles(arrivals, as_of=clock), as_of=clock)
        except ValueError:
            # An unusable historical tape cannot price the position or block a valid fill.
            logger.warning("Completed candle data unavailable for valuation", exc_info=True)
            return
        basis = basis_of(last_fill)
        for candle, metadata in zip(snapshot["candles"], snapshot["metadata"]):
            start = parse_time(candle["timestamp"])
            price_time = parse_time(metadata["interval_end"])
            if not (metadata["is_complete"] and first <= start < stop and price_time > last_fill_receipt):
                continue
            price = int((Decimal(str(candle["close"])) * MICROS_PER_DOLLAR).to_integral_value(rounding=ROUND_HALF_UP))
            if 0 < price <= MAX_SEQUENCE:
                self._record(connection, account_id=account_id, event_key=f"candle:{candle['timestamp']}",
                             event="completed_candle", event_time=iso_utc(clock), order_id=None,
                             candle_start=candle["timestamp"], price_source=CANDLE_SOURCE,
                             price_time=iso_utc(price_time), price=price, cash=account["cash_micros"],
                             shares=account["shares"], basis=basis)

    def _record(self, connection, *, account_id, event_key, event, event_time, order_id, candle_start,
                price_source, price_time, price, cash, shares, basis):
        amounts = values(cash, shares, basis, price)
        self.repository.insert(connection, {
            "account_id": account_id, "event_key": event_key, "event": event, "event_time": event_time,
            "order_id": order_id, "candle_start": candle_start, "price_source": price_source,
            "price_time": price_time, "price_micros": price, "cash_micros": cash, "shares": shares,
            "cost_basis_exact": str(basis),
            **{key: str(amounts[key]) for key in ("realized_micros", "unrealized_micros", "total_micros")},
        })

    def snapshot(self, connection, account, clock):
        history = []
        for saved in self.repository.history(connection, account["id"]):
            row = dict(saved)
            basis = Fraction(row.pop("cost_basis_exact"))
            row["average_cost_micros"] = rounded_micros(basis / row["shares"]) if row["shares"] else None
            for key in ("realized_micros", "unrealized_micros", "total_micros"):
                row[key] = int(row[key])
            history.append(row)

        latest = history[0] if history else None
        valid = bool(account["shares"] and latest is not None and
                     timedelta(0) <= clock - parse_time(latest["price_time"]) <= VALID_PRICE_AGE)
        price = latest["price_micros"] if valid else None
        basis = basis_of(self.repository.latest(connection, account["id"]))
        current = values(account["cash_micros"], account["shares"], basis, price)
        current.update({
            "valuation_status": ("priced" if valid else "unavailable") if account["shares"] else "flat",
            "price_micros": price,
            "price_time": latest["price_time"] if valid else None,
            "price_source": latest["price_source"] if valid else None,
        })
        return {"current": current, "history": history}
