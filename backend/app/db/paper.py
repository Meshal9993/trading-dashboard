"""Storage for local simulated accounts, order requests and immutable fills."""
PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_accounts (
    id TEXT PRIMARY KEY,
    cash_micros INTEGER NOT NULL DEFAULT 1000000000 CHECK(cash_micros >= 0),
    shares INTEGER NOT NULL DEFAULT 0 CHECK(shares >= 0),
    replay_start TEXT,
    replay_end TEXT,
    high_water INTEGER,
    cursor INTEGER NOT NULL DEFAULT 0,
    newest_source_ms INTEGER
);
INSERT OR IGNORE INTO paper_accounts(id) VALUES ('live');
CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES paper_accounts(id),
    request_id TEXT NOT NULL,
    symbol TEXT NOT NULL CHECK(symbol = 'AAPL'),
    side TEXT NOT NULL CHECK(side IN ('buy', 'sell', 'close')),
    quantity INTEGER NOT NULL CHECK(quantity >= 0),
    submitted_at TEXT NOT NULL,
    submitted_cursor INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'filled', 'rejected')),
    rejection_reason TEXT,
    UNIQUE(account_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_paper_pending ON paper_orders(account_id, status, id);
CREATE TABLE IF NOT EXISTS paper_fills (
    order_id INTEGER PRIMARY KEY REFERENCES paper_orders(id),
    arrival_sequence INTEGER NOT NULL REFERENCES observation_arrivals(arrival_sequence),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    price_micros INTEGER NOT NULL CHECK(price_micros > 0),
    executed_at TEXT NOT NULL
);
"""


class PaperRepository:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def account(connection, account_id):
        return connection.execute("SELECT * FROM paper_accounts WHERE id = ?", (account_id,)).fetchone()

    @staticmethod
    def create_replay(connection, account_id, start, end, high_water):
        connection.execute(
            "INSERT INTO paper_accounts(id, replay_start, replay_end, high_water) VALUES (?, ?, ?, ?)",
            (account_id, start, end, high_water),
        )

    @staticmethod
    def advance(connection, account_id, cursor, source_ms):
        connection.execute(
            "UPDATE paper_accounts SET cursor = ?, newest_source_ms = ? WHERE id = ?",
            (cursor, source_ms, account_id),
        )

    @staticmethod
    def order_for_request(connection, account_id, request_id):
        return connection.execute(
            "SELECT * FROM paper_orders WHERE account_id = ? AND request_id = ?", (account_id, request_id)
        ).fetchone()

    @staticmethod
    def insert_order(connection, account, request_id, side, quantity, submitted_at, rejection):
        connection.execute(
            """INSERT INTO paper_orders(account_id, request_id, symbol, side, quantity,
                                        submitted_at, submitted_cursor, status, rejection_reason)
               VALUES (?, ?, 'AAPL', ?, ?, ?, ?, ?, ?)""",
            (account["id"], request_id, side, quantity, submitted_at, account["cursor"],
             "rejected" if rejection else "pending", rejection),
        )

    @staticmethod
    def pending(connection, account_id):
        return connection.execute(
            "SELECT * FROM paper_orders WHERE account_id = ? AND status = 'pending' ORDER BY id",
            (account_id,),
        ).fetchall()

    @staticmethod
    def reject(connection, order_id, reason):
        connection.execute(
            "UPDATE paper_orders SET status = 'rejected', rejection_reason = ? WHERE id = ?",
            (reason, order_id),
        )

    @staticmethod
    def fill(connection, order, observation, quantity, price_micros, cash, shares, executed_at):
        connection.execute(
            """INSERT INTO paper_fills(order_id, arrival_sequence, quantity, price_micros, executed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (order["id"], observation["arrival_sequence"], quantity, price_micros, executed_at),
        )
        connection.execute(
            "UPDATE paper_orders SET status = 'filled', quantity = ? WHERE id = ?",
            (quantity, order["id"]),
        )
        connection.execute(
            "UPDATE paper_accounts SET cash_micros = ?, shares = ? WHERE id = ?",
            (cash, shares, order["account_id"]),
        )

    @staticmethod
    def orders(connection, account_id):
        return connection.execute(
            """SELECT p.id, p.request_id, p.symbol, p.side, p.quantity, p.submitted_at, p.status, p.rejection_reason,
                      f.price_micros, f.executed_at, f.arrival_sequence, o.observation_id, o.source_time_utc,
                      a.receipt_time_utc
               FROM paper_orders p
               LEFT JOIN paper_fills f ON f.order_id = p.id
               LEFT JOIN observation_arrivals a USING (arrival_sequence)
               LEFT JOIN raw_observations o ON o.observation_id = a.observation_id
               WHERE p.account_id = ? ORDER BY p.id DESC""",
            (account_id,),
        ).fetchall()

    @staticmethod
    def observation(connection, sequence):
        return connection.execute(
            """SELECT a.arrival_sequence, a.receipt_time_utc, a.disposition, o.source, o.symbol,
                      o.source_time_ms, o.source_time_utc, o.payload_json
               FROM observation_arrivals a JOIN raw_observations o USING (observation_id)
               WHERE a.arrival_sequence = ?""", (sequence,),
        ).fetchone()

    @staticmethod
    def newer_live_observations(connection, newest):
        return connection.execute(
            """SELECT a.arrival_sequence, a.receipt_time_utc, a.disposition, o.source, o.symbol,
                      o.source_time_ms, o.payload_json
               FROM raw_observations o JOIN observation_arrivals a USING (observation_id)
               WHERE o.source = 'yahoo_finance_websocket' AND o.symbol = 'AAPL' AND a.disposition = 'inserted'
                 AND o.source_time_ms > ?
               ORDER BY o.source_time_ms DESC""", (newest if newest is not None else -1,),
        )


PNL_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_pnl_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES paper_accounts(id),
    symbol TEXT NOT NULL CHECK(symbol = 'AAPL'),
    event_key TEXT NOT NULL,
    event TEXT NOT NULL CHECK(event IN ('fill', 'completed_candle')),
    event_time TEXT NOT NULL,
    order_id INTEGER REFERENCES paper_fills(order_id),
    candle_start TEXT,
    price_source TEXT NOT NULL,
    price_time TEXT NOT NULL,
    price_micros INTEGER NOT NULL CHECK(price_micros > 0),
    cash_micros INTEGER NOT NULL,
    shares INTEGER NOT NULL,
    cost_basis_exact TEXT NOT NULL,
    realized_micros TEXT NOT NULL,
    unrealized_micros TEXT NOT NULL,
    total_micros TEXT NOT NULL,
    UNIQUE(account_id, event_key),
    CHECK((event = 'fill' AND order_id IS NOT NULL AND candle_start IS NULL)
       OR (event = 'completed_candle' AND order_id IS NULL AND candle_start IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_paper_pnl_events ON paper_pnl_history(account_id, event, id DESC);
"""


class PaperPnLRepository:
    @staticmethod
    def latest(connection, account_id, event=None):
        query = "SELECT * FROM paper_pnl_history WHERE account_id = ?"
        params = [account_id]
        if event is not None:
            query += " AND event = ?"
            params.append(event)
        return connection.execute(query + " ORDER BY id DESC LIMIT 1", params).fetchone()

    @staticmethod
    def history(connection, account_id):
        return connection.execute(
            "SELECT * FROM paper_pnl_history WHERE account_id = ? ORDER BY id DESC", (account_id,)
        ).fetchall()

    @staticmethod
    def insert(connection, values):
        connection.execute(
            """INSERT INTO paper_pnl_history(
                   account_id, symbol, event_key, event, event_time, order_id, candle_start,
                   price_source, price_time, price_micros, cash_micros, shares, cost_basis_exact,
                   realized_micros, unrealized_micros, total_micros)
               VALUES (:account_id, 'AAPL', :event_key, :event, :event_time, :order_id, :candle_start,
                       :price_source, :price_time, :price_micros, :cash_micros, :shares, :cost_basis_exact,
                       :realized_micros, :unrealized_micros, :total_micros)
               ON CONFLICT(account_id, event_key) DO NOTHING""", values,
        )

    @staticmethod
    def legacy_fills(connection):
        return connection.execute(
            """SELECT p.account_id, p.side, f.order_id, f.quantity, f.price_micros, f.executed_at,
                      a.receipt_time_utc, o.source_time_utc
               FROM paper_fills f JOIN paper_orders p ON p.id = f.order_id
               JOIN observation_arrivals a ON a.arrival_sequence = f.arrival_sequence
               JOIN raw_observations o ON o.observation_id = a.observation_id
               WHERE NOT EXISTS (SELECT 1 FROM paper_pnl_history l
                                 WHERE l.account_id = p.account_id AND l.order_id = f.order_id)
               ORDER BY f.arrival_sequence, f.order_id"""
        ).fetchall()

    @staticmethod
    def has_completed_price(connection, first_ms, stop_ms, clock, cursor):
        return connection.execute(
            """SELECT 1 FROM raw_observations o JOIN observation_arrivals a USING (observation_id)
               WHERE o.symbol = 'AAPL' AND o.source = 'yahoo_finance_websocket'
                 AND o.source_time_ms >= ? AND o.source_time_ms < ?
                 AND a.receipt_time_utc <= ? AND a.arrival_sequence <= ?
               LIMIT 1""", (first_ms, stop_ms, clock, cursor)
        ).fetchone() is not None

    @staticmethod
    def fill_receipt(connection, order_id):
        return connection.execute(
            """SELECT a.receipt_time_utc FROM paper_fills f
               JOIN observation_arrivals a USING (arrival_sequence) WHERE f.order_id = ?""", (order_id,)
        ).fetchone()[0]
