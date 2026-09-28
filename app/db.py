"""SQLite access layer. Trades are written inside an explicit ACID transaction."""
import os
import sqlite3
from pathlib import Path

DB_PATH = os.environ.get("PORTFOLIO_DB", "portfolio.db")
SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"


class TradeError(ValueError):
    """Raised when a trade violates a business rule (e.g. selling more than held)."""


def get_conn(path: str | None = None) -> sqlite3.Connection:
    # isolation_level=None -> we control BEGIN/COMMIT explicitly.
    conn = sqlite3.connect(path or DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text())


def record_trade(conn, portfolio_id: int, symbol: str, side: str,
                 quantity: float, price: float, trade_date: str) -> int:
    """Insert a trade atomically.

    BEGIN IMMEDIATE takes the write lock up front, so the 'do I hold enough
    shares?' check and the INSERT cannot be interleaved with another writer
    (no lost updates / oversell race). Any failure rolls the whole thing back.
    """
    side = side.upper()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if side == "SELL":
            held = conn.execute(
                """SELECT COALESCE(SUM(CASE side WHEN 'BUY' THEN quantity ELSE -quantity END), 0)
                   FROM transactions WHERE portfolio_id = ? AND symbol = ?""",
                (portfolio_id, symbol),
            ).fetchone()[0]
            if quantity > held + 1e-9:
                raise TradeError(f"Cannot sell {quantity} {symbol}: only {held} held")
        cur = conn.execute(
            """INSERT INTO transactions (portfolio_id, symbol, side, quantity, price, trade_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (portfolio_id, symbol, side, quantity, price, trade_date),
        )
        conn.execute("COMMIT")
        return cur.lastrowid
    except Exception:
        conn.execute("ROLLBACK")
        raise
