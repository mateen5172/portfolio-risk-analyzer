"""Streamlit dashboard (the deployable live demo).  Run: streamlit run dashboard.py"""
import os

import pandas as pd
import streamlit as st

from app import analytics as an
from app.db import DB_PATH, TradeError, get_conn, record_trade
from seed import seed

st.set_page_config(page_title="Portfolio Risk Analyzer", page_icon="📈", layout="wide")
if not os.path.exists(DB_PATH):
    seed()

conn = get_conn()
st.title("📈 Portfolio Tracker & Risk Analyzer")
st.caption("Synthetic data only: fictional companies, simulated prices. Not investment advice.")

portfolios = {r["name"]: r["id"] for r in conn.execute("SELECT * FROM portfolios ORDER BY id")}
with st.sidebar:
    name = st.selectbox("Portfolio", list(portfolios))
    rf = st.slider("Risk-free rate (annual)", 0.0, 0.10, 0.03, 0.005, format="%.3f")
    conf = st.select_slider("VaR confidence", [0.90, 0.95, 0.99], value=0.95)
pid = portfolios[name]

rep = an.full_report(conn, pid, rf, conf)
risk, conc = rep["risk"], rep["concentration"]

c = st.columns(6)
c[0].metric("Portfolio value", f"${rep['value'].iloc[-1]:,.0f}")
c[1].metric("Ann. return", f"{risk['annualized_return']:.1%}")
c[2].metric("Ann. volatility", f"{risk['annualized_volatility']:.1%}")
c[3].metric("Sharpe", f"{risk['sharpe_ratio']:.2f}")
c[4].metric(f"1-day VaR {conf:.0%}", f"{risk['var_historical_1d']:.2%}")
c[5].metric("Max drawdown", f"{risk['max_drawdown']:.1%}")

left, right = st.columns(2)
with left:
    st.subheader("Growth of $1 vs synthetic market")
    prices = an.load_prices(conn)
    port = (1 + rep["returns"]).cumprod()
    mkt = (1 + prices["MKT"].pct_change().dropna()).cumprod().reindex(port.index)
    st.line_chart(pd.DataFrame({"Portfolio": port, "Market": mkt / mkt.iloc[0] * port.iloc[0]}))
with right:
    st.subheader("Drawdown")
    curve = (1 + rep["returns"]).cumprod()
    st.area_chart(curve / curve.cummax() - 1)

st.subheader("Holdings")
st.dataframe(rep["holdings"], use_container_width=True, hide_index=True)

a, b, d = st.columns(3)
with a:
    st.subheader("Sector weights")
    st.bar_chart(pd.Series(conc["sector_weights"]))
    st.caption(f"HHI {conc['hhi']} ≈ {conc['effective_positions']} equally-sized positions")
with b:
    st.subheader("Other risk metrics")
    st.json({k: risk[k] for k in ("sortino_ratio", "cvar_historical_1d",
                                  "var_parametric_1d", "beta", "correlation_to_benchmark")})
with d:
    st.subheader("Correlation")
    st.dataframe(rep["correlation"].style.background_gradient(cmap="RdYlGn_r", vmin=-1, vmax=1)
                 .format("{:.2f}"), use_container_width=True)

with st.expander("Record a trade (try selling more than you hold → rejected atomically)"):
    syms = [r[0] for r in conn.execute("SELECT symbol FROM assets WHERE symbol <> 'MKT'")]
    f1, f2, f3, f4 = st.columns(4)
    sym, side = f1.selectbox("Symbol", syms), f2.selectbox("Side", ["BUY", "SELL"])
    qty, px = f3.number_input("Quantity", 1.0, value=10.0), f4.number_input("Price", 1.0, value=50.0)
    if st.button("Submit trade"):
        try:
            record_trade(conn, pid, sym, side, qty, px, str(prices.index[-1].date()))
            st.success("Trade committed. Reload to see updated metrics.")
        except TradeError as e:
            st.error(f"Rolled back: {e}")
