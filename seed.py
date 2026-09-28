"""Generate 100% synthetic demo data (fictional companies, simulated prices).

Prices follow a one-factor model: each stock = beta * market shock + idiosyncratic
shock, compounded as Geometric Brownian Motion. Fixed random seed => reproducible.
"""
import sys

import numpy as np
import pandas as pd

from app.db import get_conn, init_db, record_trade

ASSETS = [  # symbol, fictional name, sector, beta, annual drift, annual idio-vol, start price
    ("NOVA", "Nova Systems (fictional)",      "Technology",  1.30, 0.14, 0.20, 120.0),
    ("QNTM", "Quantum Loop (fictional)",      "Technology",  1.50, 0.16, 0.28,  75.0),
    ("HLTH", "HelixCare (fictional)",         "Healthcare",  0.80, 0.09, 0.15,  95.0),
    ("BNKR", "Bankroll Trust (fictional)",    "Financials",  1.10, 0.08, 0.18,  55.0),
    ("ENRG", "Ember Energy (fictional)",      "Energy",      0.90, 0.06, 0.25,  60.0),
    ("STPL", "Staple Foods (fictional)",      "Consumer",    0.55, 0.05, 0.10,  70.0),
    ("UTIL", "Grid Utilities (fictional)",    "Utilities",   0.40, 0.04, 0.09,  48.0),
    ("RETL", "Retail Rocket (fictional)",     "Consumer",    1.20, 0.10, 0.22,  40.0),
]
MARKET = ("MKT", "Synthetic Market Index", "Benchmark", 1.0, 0.09, 0.0, 1000.0)


def generate_prices(start="2023-01-02", periods=780, seed=42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=periods)
    dt = 1 / 252
    mkt_vol = 0.16
    mkt_shock = rng.standard_normal(periods)
    out = {}
    for sym, _, _, beta, mu, idio, p0 in ASSETS + [MARKET]:
        idio_shock = rng.standard_normal(periods)
        sigma_total = np.sqrt((beta * mkt_vol) ** 2 + idio ** 2)
        z = (beta * mkt_vol * mkt_shock + idio * idio_shock) / sigma_total
        log_ret = (mu - 0.5 * sigma_total ** 2) * dt + sigma_total * np.sqrt(dt) * z
        out[sym] = p0 * np.exp(np.cumsum(log_ret))
    return pd.DataFrame(out, index=dates).round(2)


def seed(db_path: str | None = None, reset: bool = False) -> None:
    conn = get_conn(db_path)
    if reset:
        for obj in ("v_positions", "v_daily_returns"):
            conn.execute(f"DROP VIEW IF EXISTS {obj}")
        for tbl in ("transactions", "portfolios", "prices", "assets"):
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    init_db(conn)
    if conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]:
        return  # already seeded

    prices = generate_prices()
    conn.execute("BEGIN")
    conn.executemany("INSERT INTO assets VALUES (?, ?, ?)",
                     [(a[0], a[1], a[2]) for a in ASSETS + [MARKET]])
    rows = [(sym, d.strftime("%Y-%m-%d"), float(px))
            for sym in prices.columns for d, px in prices[sym].items()]
    conn.executemany("INSERT INTO prices VALUES (?, ?, ?)", rows)
    conn.execute("COMMIT")

    conn.execute("INSERT INTO portfolios (name) VALUES ('Growth Demo')")
    conn.execute("INSERT INTO portfolios (name) VALUES ('Conservative Demo')")

    def px(sym, day):  # execution price = that day's close
        return float(prices.loc[day, sym])

    d0 = prices.index[5].strftime("%Y-%m-%d")
    d1 = prices.index[260].strftime("%Y-%m-%d")
    d2 = prices.index[520].strftime("%Y-%m-%d")
    plan = {
        1: [("NOVA", "BUY", 100, d0), ("QNTM", "BUY", 120, d0), ("RETL", "BUY", 200, d0),
            ("HLTH", "BUY", 80, d0), ("BNKR", "BUY", 150, d1), ("QNTM", "SELL", 40, d2),
            ("ENRG", "BUY", 100, d2)],
        2: [("STPL", "BUY", 150, d0), ("UTIL", "BUY", 200, d0), ("HLTH", "BUY", 120, d0),
            ("BNKR", "BUY", 100, d1), ("STPL", "SELL", 30, d2), ("NOVA", "BUY", 30, d2)],
    }
    for pid, trades in plan.items():
        for sym, side, qty, day in trades:
            record_trade(conn, pid, sym, side, qty, px(sym, day), day)
    conn.close()


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
    print("Seeded dummy data.")
