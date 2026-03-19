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
    strategy_params TEXT,
    arm_id INTEGER
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
    end_balance REAL,
    paused_until TEXT,
    peak_balance REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS arm_performance (
    arm_id TEXT,
    timestamp INTEGER,
    r_multiple REAL,
    pnl_percent REAL,
    outcome TEXT -- WIN/LOSS
);

CREATE TABLE IF NOT EXISTS polymarket_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER,
    market_key TEXT,
    probability REAL,
    risk_scale REAL
);

CREATE TABLE IF NOT EXISTS scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    score REAL,
    rsi REAL,
    atr_pct REAL,
    volume_rank INTEGER,
    trend TEXT,
    regime TEXT,
    scanned_at INTEGER
);

CREATE TABLE IF NOT EXISTS committee_decisions (
    id              TEXT PRIMARY KEY,
    timestamp       INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    approved        INTEGER DEFAULT 0,
    final_score     REAL DEFAULT 0,
    size_multiplier REAL DEFAULT 1.0,
    veto_by         TEXT,
    veto_reason     TEXT,
    verdicts_json   TEXT,
    trade_executed  INTEGER DEFAULT 0,
    trade_id        TEXT
);

CREATE TABLE IF NOT EXISTS trade_features (
    id              TEXT PRIMARY KEY,
    trade_id        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    -- Price features
    price           REAL,
    price_change_1h REAL,
    price_change_4h REAL,
    price_change_24h REAL,
    -- Momentum features
    rsi_14          REAL,
    rsi_7           REAL,
    macd            REAL,
    macd_signal     REAL,
    macd_hist       REAL,
    momentum_7d     REAL,
    -- Trend features
    ema_fast        REAL,
    ema_slow        REAL,
    ema_fast_slope  REAL,
    ema_slow_slope  REAL,
    adx             REAL,
    -- Volatility features
    atr             REAL,
    atr_percent     REAL,
    bb_position     REAL,
    bb_width        REAL,
    -- Volume features
    volume_ratio    REAL,
    volume_24h      REAL,
    -- Setup quality features
    scanner_score   REAL,
    breakout_detected INTEGER,
    regime          TEXT,
    -- Macro features
    fear_greed      REAL,
    macro_scale     REAL,
    btc_dominance   REAL,
    -- Market context
    hour_of_day     INTEGER,
    day_of_week     INTEGER,
    -- Outcome (filled when trade closes)
    outcome         INTEGER,
    pnl             REAL,
    pnl_percent     REAL,
    hold_hours      REAL,
    exit_reason     TEXT,
    -- Timestamps
    captured_at     INTEGER NOT NULL
);
