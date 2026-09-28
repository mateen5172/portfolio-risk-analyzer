-- Stock Portfolio Tracker & Risk Analyzer: schema (SQLite; portable to PostgreSQL)
-- All data is DUMMY / synthetic. No real accounts, banks or market data.

CREATE TABLE IF NOT EXISTS assets (
    symbol  TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    sector  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    symbol  TEXT NOT NULL REFERENCES assets(symbol),
    date    TEXT NOT NULL,                       -- ISO date YYYY-MM-DD
    close   REAL NOT NULL CHECK (close > 0),
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS portfolios (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
    symbol       TEXT    NOT NULL REFERENCES assets(symbol),
    side         TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity     REAL    NOT NULL CHECK (quantity > 0),
    price        REAL    NOT NULL CHECK (price > 0),
    trade_date   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tx_portfolio ON transactions(portfolio_id, trade_date);

-- Net position per portfolio/symbol (BUY adds, SELL subtracts).
CREATE VIEW IF NOT EXISTS v_positions AS
SELECT portfolio_id,
       symbol,
       SUM(CASE side WHEN 'BUY' THEN quantity ELSE -quantity END) AS net_quantity
FROM transactions
GROUP BY portfolio_id, symbol
HAVING net_quantity <> 0;

-- Daily simple returns using a window function (LAG).
CREATE VIEW IF NOT EXISTS v_daily_returns AS
SELECT symbol,
       date,
       close,
       close / LAG(close) OVER (PARTITION BY symbol ORDER BY date) - 1.0 AS daily_return
FROM prices;
