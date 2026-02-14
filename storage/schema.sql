CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT,
    timestamp INTEGER,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    symbol TEXT,
    side TEXT,
    order_type TEXT,
    amount REAL,
    price REAL,
    status TEXT,
    filled_amount REAL,
    filled_price REAL,
    timestamp INTEGER,
    client_order_id TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    symbol TEXT,
    side TEXT,
    entry_price REAL,
    amount REAL,
    stop_loss REAL,
    take_profit REAL,
    entry_time INTEGER,
    status TEXT,
    exit_price REAL,
    exit_time INTEGER,
    exit_reason TEXT,
    pnl REAL,
    pnl_percent REAL,
    commission REAL,
    strategy_params TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    position_id TEXT,
    symbol TEXT,
    side TEXT,
    price REAL,
    amount REAL,
    commission REAL,
    timestamp INTEGER,
    reason TEXT,
    FOREIGN KEY(position_id) REFERENCES positions(id)
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    pnl REAL,
    trades_count INTEGER,
    wins INTEGER,
    losses INTEGER,
    max_drawdown REAL,
    start_balance REAL,
    end_balance REAL
);

CREATE TABLE IF NOT EXISTS arm_performance (
    arm_id TEXT,
    timestamp INTEGER,
    r_multiple REAL,
    pnl_percent REAL,
    outcome TEXT -- WIN/LOSS
);
