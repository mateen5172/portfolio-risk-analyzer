"""Portfolio analytics: holdings, P&L and risk metrics (pandas/numpy)."""
from statistics import NormalDist

import numpy as np
import pandas as pd

TRADING_DAYS = 252


# ---------- data loading ----------
def load_prices(conn, symbols=None) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT symbol, date, close FROM prices", conn)
    df["date"] = pd.to_datetime(df["date"])
    if symbols is not None:
        df = df[df["symbol"].isin(symbols)]
    return df.pivot(index="date", columns="symbol", values="close").sort_index()


def load_trades(conn, portfolio_id: int) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT * FROM transactions WHERE portfolio_id = ? ORDER BY trade_date, id",
        conn, params=(portfolio_id,),
    )
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


# ---------- holdings & P&L ----------
def compute_holdings(trades: pd.DataFrame, last_prices: pd.Series, sectors: dict) -> pd.DataFrame:
    """Weighted-average-cost accounting. SELLs realise P&L at the running average cost."""
    book: dict[str, dict] = {}
    for t in trades.itertuples():
        pos = book.setdefault(t.symbol, {"qty": 0.0, "cost": 0.0, "realized": 0.0})
        if t.side == "BUY":
            pos["qty"] += t.quantity
            pos["cost"] += t.quantity * t.price
        else:
            avg = pos["cost"] / pos["qty"]
            pos["realized"] += t.quantity * (t.price - avg)
            pos["qty"] -= t.quantity
            pos["cost"] -= t.quantity * avg

    rows = []
    for sym, p in book.items():
        if p["qty"] < 1e-9 and abs(p["realized"]) < 1e-9:
            continue
        px = float(last_prices.get(sym, np.nan))
        mv = p["qty"] * px
        rows.append({
            "symbol": sym, "sector": sectors.get(sym, "Unknown"),
            "quantity": round(p["qty"], 4),
            "avg_cost": round(p["cost"] / p["qty"], 4) if p["qty"] > 1e-9 else 0.0,
            "last_price": round(px, 4), "market_value": round(mv, 2),
            "unrealized_pnl": round(mv - p["cost"], 2),
            "realized_pnl": round(p["realized"], 2),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        total = df["market_value"].sum()
        df["weight"] = (df["market_value"] / total).round(4) if total else 0.0
    return df


# ---------- time series ----------
def quantity_history(trades: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Shares held at each close, per symbol."""
    signed = trades.assign(q=np.where(trades["side"] == "BUY", trades["quantity"], -trades["quantity"]))
    daily = signed.groupby(["trade_date", "symbol"])["q"].sum().unstack(fill_value=0.0)
    daily = daily.reindex(daily.index.union(dates)).fillna(0.0).cumsum()
    return daily.reindex(dates)


def portfolio_returns(qty: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Return (value_series, daily_returns).

    Return_t = P&L on positions held at t-1 close / value at t-1 close.
    Trades therefore don't distort performance (time-weighted return).
    """
    prices = prices[qty.columns]
    value = (qty * prices).sum(axis=1)
    pnl = (qty.shift(1) * prices.diff()).sum(axis=1)
    prev = value.shift(1)
    rets = (pnl / prev).where(prev > 0).dropna()
    return value, rets


# ---------- risk metrics ----------
def max_drawdown(returns: pd.Series) -> float:
    curve = (1 + returns).cumprod()
    return float((curve / curve.cummax() - 1).min())


def risk_metrics(returns: pd.Series, benchmark: pd.Series | None = None,
                 rf: float = 0.03, confidence: float = 0.95) -> dict:
    r = returns.dropna()
    n = len(r)
    if n < 2:
        raise ValueError("Not enough return observations")
    rf_d = rf / TRADING_DAYS
    excess = r - rf_d
    vol = r.std(ddof=1) * np.sqrt(TRADING_DAYS)
    ann_ret = (1 + r).prod() ** (TRADING_DAYS / n) - 1
    downside = np.sqrt(np.mean(np.minimum(excess, 0.0) ** 2)) * np.sqrt(TRADING_DAYS)

    alpha = 1 - confidence
    q = r.quantile(alpha)
    tail = r[r <= q]
    z = NormalDist().inv_cdf(confidence)

    out = {
        "observations": n,
        "annualized_return": round(float(ann_ret), 4),
        "annualized_volatility": round(float(vol), 4),
        "sharpe_ratio": round(float(excess.mean() / r.std(ddof=1) * np.sqrt(TRADING_DAYS)), 3)
        if r.std(ddof=1) > 0 else None,
        "sortino_ratio": round(float(excess.mean() * TRADING_DAYS / downside), 3) if downside > 0 else None,
        "max_drawdown": round(max_drawdown(r), 4),
        "var_historical_1d": round(float(-q), 4),
        "cvar_historical_1d": round(float(-tail.mean()), 4) if len(tail) else None,
        "var_parametric_1d": round(float(z * r.std(ddof=1) - r.mean()), 4),
        "confidence": confidence,
        "risk_free_rate": rf,
    }
    if benchmark is not None:
        a = pd.concat([r, benchmark], axis=1, join="inner").dropna()
        if len(a) > 2 and a.iloc[:, 1].var() > 0:
            out["beta"] = round(float(a.iloc[:, 0].cov(a.iloc[:, 1]) / a.iloc[:, 1].var()), 3)
            out["correlation_to_benchmark"] = round(float(a.iloc[:, 0].corr(a.iloc[:, 1])), 3)
    return out


def concentration(holdings: pd.DataFrame) -> dict:
    """Herfindahl-Hirschman Index (sum of squared weights) + sector weights."""
    if holdings.empty:
        return {"hhi": None, "effective_positions": None, "sector_weights": {}}
    open_pos = holdings[holdings["market_value"] > 0]
    w = open_pos["market_value"] / open_pos["market_value"].sum()
    hhi = float((w ** 2).sum())
    sectors = open_pos.assign(w=w).groupby("sector")["w"].sum().round(4).to_dict()
    return {"hhi": round(hhi, 4), "effective_positions": round(1 / hhi, 2), "sector_weights": sectors}


def correlation_matrix(qty: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    held = [c for c in qty.columns if (qty[c] != 0).any()]
    return prices[held].pct_change().dropna().corr().round(3)


# ---------- one-call report ----------
def full_report(conn, portfolio_id: int, rf: float = 0.03, confidence: float = 0.95,
                benchmark_symbol: str = "MKT") -> dict:
    trades = load_trades(conn, portfolio_id)
    if trades.empty:
        raise ValueError("Portfolio has no trades")
    prices = load_prices(conn)
    sectors = dict(conn.execute("SELECT symbol, sector FROM assets").fetchall())
    holdings = compute_holdings(trades, prices.ffill().iloc[-1], sectors)
    qty = quantity_history(trades, prices.index)
    value, rets = portfolio_returns(qty, prices)
    bench = prices[benchmark_symbol].pct_change().dropna() if benchmark_symbol in prices else None
    return {
        "holdings": holdings, "value": value, "returns": rets,
        "risk": risk_metrics(rets, bench, rf, confidence),
        "concentration": concentration(holdings),
        "correlation": correlation_matrix(qty, prices),
    }
