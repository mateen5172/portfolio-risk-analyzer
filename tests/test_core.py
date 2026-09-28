"""Run with:  python -m unittest discover -s tests   (or: pytest)"""
import unittest

import numpy as np
import pandas as pd

from app import analytics as an
from app.db import TradeError, get_conn, init_db, record_trade
from seed import seed


class AnalyticsMath(unittest.TestCase):
    def test_zero_volatility_series(self):
        r = pd.Series([0.001] * 50)
        m = an.risk_metrics(r, rf=0.0)
        self.assertAlmostEqual(m["annualized_volatility"], 0.0, places=6)

    def test_max_drawdown_known_value(self):
        # 100 -> 120 -> 60 : drawdown = 60/120 - 1 = -50%
        r = pd.Series([0.20, -0.50])
        self.assertAlmostEqual(an.max_drawdown(r), -0.50, places=6)

    def test_sharpe_matches_hand_calc(self):
        rng = np.random.default_rng(1)
        r = pd.Series(rng.normal(0.0005, 0.01, 500))
        expected = (r.mean() - 0.02 / 252) / r.std(ddof=1) * np.sqrt(252)
        self.assertAlmostEqual(an.risk_metrics(r, rf=0.02)["sharpe_ratio"], round(expected, 3), places=3)

    def test_beta_of_benchmark_with_itself_is_one(self):
        rng = np.random.default_rng(2)
        m = pd.Series(rng.normal(0, 0.01, 300))
        self.assertAlmostEqual(an.risk_metrics(m, m)["beta"], 1.0, places=3)

    def test_hhi_equal_weights(self):
        df = pd.DataFrame({"market_value": [100.0] * 4, "sector": list("ABCD")})
        c = an.concentration(df)
        self.assertAlmostEqual(c["hhi"], 0.25)
        self.assertAlmostEqual(c["effective_positions"], 4.0)

    def test_weighted_average_cost_and_realized_pnl(self):
        t = pd.DataFrame([
            dict(symbol="X", side="BUY", quantity=10, price=10.0),
            dict(symbol="X", side="BUY", quantity=10, price=20.0),   # avg cost 15
            dict(symbol="X", side="SELL", quantity=10, price=25.0),  # realises 10*(25-15)=100
        ])
        h = an.compute_holdings(t, pd.Series({"X": 30.0}), {"X": "Test"}).iloc[0]
        self.assertEqual(h["quantity"], 10)
        self.assertAlmostEqual(h["avg_cost"], 15.0)
        self.assertAlmostEqual(h["realized_pnl"], 100.0)
        self.assertAlmostEqual(h["unrealized_pnl"], 150.0)


class Database(unittest.TestCase):
    def setUp(self):
        self.conn = get_conn(":memory:")
        init_db(self.conn)
        self.conn.execute("INSERT INTO assets VALUES ('X','Test','Tech')")
        self.conn.execute("INSERT INTO portfolios (name) VALUES ('P')")

    def test_oversell_rejected_and_rolled_back(self):
        record_trade(self.conn, 1, "X", "BUY", 10, 5.0, "2024-01-02")
        with self.assertRaises(TradeError):
            record_trade(self.conn, 1, "X", "SELL", 11, 5.0, "2024-01-03")
        n = self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        self.assertEqual(n, 1)  # failed trade left no trace (atomicity)

    def test_check_constraint_blocks_negative_quantity(self):
        with self.assertRaises(Exception):
            record_trade(self.conn, 1, "X", "BUY", -5, 5.0, "2024-01-02")
        # connection is still usable afterwards (rollback happened)
        record_trade(self.conn, 1, "X", "BUY", 1, 5.0, "2024-01-02")


class EndToEnd(unittest.TestCase):
    def test_seeded_report_is_sane(self):
        import os, tempfile
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        seed(path)
        conn = get_conn(path)
        rep = an.full_report(conn, 1)
        risk = rep["risk"]
        self.assertGreater(risk["annualized_volatility"], 0.05)
        self.assertLess(risk["max_drawdown"], 0)
        self.assertGreaterEqual(risk["var_historical_1d"], 0)
        self.assertGreaterEqual(risk["cvar_historical_1d"], risk["var_historical_1d"])
        self.assertAlmostEqual(rep["holdings"]["weight"].sum(), 1.0, places=2)
        # growth portfolio should be riskier than the conservative one
        cons = an.full_report(conn, 2)["risk"]
        self.assertGreater(risk["annualized_volatility"], cons["annualized_volatility"])


if __name__ == "__main__":
    unittest.main()
