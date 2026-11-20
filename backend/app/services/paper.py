"""Paper execution from newly observed prices with no broker or fee model."""
import asyncio
import json
import logging
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from backend.app.db.raw_observations import SOURCE
from backend.app.services.minute_candles import aware_utc, parse_time
from backend.app.services.pnl import PaperPnL

MICROS_PER_DOLLAR = 1_000_000
SQLITE_MAX_INT = (1 << 63) - 1
logger = logging.getLogger(__name__)


class PaperConflict(ValueError):
    pass


class PaperAccountMissing(ValueError):
    pass


def iso_utc(time):
    return aware_utc(time).isoformat(timespec="microseconds")


def balances_after_fill(cash, shares, side, quantity, price_micros):
    """Shared zero-fee, long-only whole-share execution rules."""
    amount = quantity * price_micros
    if side == "buy":
        if amount > cash:
            raise ValueError("Insufficient cash at observed price")
        cash, shares = cash - amount, shares + quantity
    else:
        if not 0 < quantity <= shares:
            raise ValueError("Insufficient shares at execution")
        cash, shares = cash + amount, shares - quantity
    if max(cash, shares) > SQLITE_MAX_INT:
        raise ValueError("Account limit exceeded")
    return cash, shares


def execute_paper_order(repository, pnl, connection, order, account, observation, price_micros, executed_at):
    """Apply one queued order inside the caller's SQLite write transaction."""
    side = order["side"]
    quantity = account["shares"] if side == "close" else order["quantity"]
    try:
        cash, shares = balances_after_fill(account["cash_micros"], account["shares"], side, quantity, price_micros)
    except ValueError as error:
        repository.reject(connection, order["id"], str(error))
        return
    repository.fill(connection, order, observation, quantity, price_micros, cash, shares, executed_at)
    pnl.fill(connection, account_id=account["id"], order_id=order["id"], side=side, quantity=quantity,
             cash=cash, shares=shares, price=price_micros, event_time=executed_at,
             price_time=observation["source_time_utc"])


class PaperTrading:
    def __init__(self, repository, clock):
        self.repository = repository
        self.clock = clock
        self.pnl = PaperPnL(repository.database)

    def account(self, connection, account_id):
        account = self.repository.account(connection, account_id)
        if account is None:
            raise PaperAccountMissing("Paper replay account has not been started")
        return account

    def snapshot(self, account_id):
        with self.repository.database.connect() as connection:
            connection.execute("BEGIN")
            return self._snapshot(connection, account_id)

    def _snapshot(self, connection, account_id):
        account = self.account(connection, account_id)
        return {
            "account_id": account_id, "cash_micros": account["cash_micros"], "shares": account["shares"],
            "orders": [dict(row) for row in self.repository.orders(connection, account_id)],
            "pnl": self.pnl.snapshot(connection, account, self._market_clock(connection, account)),
        }

    def _market_clock(self, connection, account):
        if account["id"] == "live":
            return aware_utc(self.clock())
        value = connection.execute(
            "SELECT max(receipt_time_utc) FROM observation_arrivals WHERE arrival_sequence <= ?",
            (account["cursor"],),
        ).fetchone()[0]
        return parse_time(value or account["replay_start"])

    def submit(self, account_id, request):
        with self.repository.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = self.account(connection, account_id)
            request_id = str(request.request_id)
            existing = self.repository.order_for_request(connection, account_id, request_id)
            if existing:
                if existing["side"] != request.side or (
                    request.side != "close" and existing["quantity"] != request.quantity
                ):
                    raise PaperConflict("Request ID already used for a different order")
            else:
                quantity = account["shares"] if request.side == "close" else request.quantity
                rejection = None
                if request.side != "buy" and not 0 < quantity <= account["shares"]:
                    rejection = "Insufficient shares"
                elif request.side == "buy" and account["cash_micros"] == 0:
                    rejection = "Insufficient cash"
                self.repository.insert_order(connection, account, request_id, request.side, quantity,
                                             iso_utc(self.clock()), rejection)
            return self._snapshot(connection, account_id)

    def observe_live(self, connection, recorded):
        # The quote and any resulting account change share the raw writer's transaction.
        observation = self.repository.observation(connection, recorded.arrival_sequence)
        self.pnl.completed(connection, self.account(connection, "live"),
                           parse_time(observation["receipt_time_utc"]), recorded.arrival_sequence)
        self._observe(connection, "live", recorded.arrival_sequence)

    def initialize_live(self):
        with self.repository.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.pnl.backfill(connection)
            account = self.account(connection, "live")
            newest = account["newest_source_ms"]
            candidates = self.repository.newer_live_observations(connection, newest)
            for observation in candidates:
                if self._eligible_price(observation, newest) is not None:
                    self.repository.advance(connection, "live", account["cursor"], observation["source_time_ms"])
                    break

    def mark_live(self):
        with self.repository.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.pnl.completed(connection, self.account(connection, "live"), aware_utc(self.clock()))

    async def run_valuations(self):
        while True:
            try:
                await asyncio.to_thread(self.mark_live)
            except (sqlite3.Error, ValueError):
                logger.warning("Paper valuation failed", exc_info=True)
            await asyncio.sleep(2)

    def apply_replay_frame(self, account_id, *, start, end, previous, replay, advance):
        with self.repository.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = self.repository.account(connection, account_id)
            selection = (iso_utc(start), iso_utc(end), replay["high_water"])
            if account is None:
                if previous != 0 or advance:
                    raise PaperConflict("Load the replay selection before advancing")
                self.repository.create_replay(connection, account_id, *selection)
                account = self.account(connection, account_id)
            if selection != (account["replay_start"], account["replay_end"], account["high_water"]):
                raise PaperConflict("Replay selection changed. Start a new paper replay.")
            current = replay["cursor"]
            if current == account["cursor"]:
                return  # Retrying a step or changing chart settings never fills again.
            if previous != account["cursor"] or not advance or current <= previous:
                raise PaperConflict("Paper replay must advance from the current cursor")
            self.pnl.completed(connection, account, parse_time(replay["clock"]), current)
            self._observe(connection, account_id, current)

    def _observe(self, connection, account_id, sequence):
        account = self.account(connection, account_id)
        observation = self.repository.observation(connection, sequence)
        newest = account["newest_source_ms"]
        price_micros = self._eligible_price(observation, newest)
        if price_micros is not None:
            newest = observation["source_time_ms"]
            executed_at = observation["receipt_time_utc"] if account_id == "live" else iso_utc(self.clock())
            for order in self.repository.pending(connection, account_id):
                if sequence <= order["submitted_cursor"]:
                    continue
                if account_id == "live" and observation["receipt_time_utc"] <= order["submitted_at"]:
                    continue
                current = self.account(connection, account_id)
                execute_paper_order(self.repository, self.pnl, connection, order, current, observation, price_micros, executed_at)
        self.repository.advance(connection, account_id, sequence, newest)

    @staticmethod
    def _eligible_price(observation, newest):
        if observation["source"] != SOURCE or observation["symbol"] != "AAPL" or observation["disposition"] != "inserted":
            return None
        event = observation["source_time_ms"]
        if (newest is not None and event < newest) or event > parse_time(observation["receipt_time_utc"]).timestamp() * 1000:
            return None
        try:
            raw = json.loads(observation["payload_json"])["price"]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return None
            price = Decimal(str(raw))
            if not price.is_finite() or not 0 < price <= Decimal(SQLITE_MAX_INT) / MICROS_PER_DOLLAR:
                return None
            amount = int((price * MICROS_PER_DOLLAR).to_integral_value(rounding=ROUND_HALF_UP))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return None
        return amount if amount > 0 else None
