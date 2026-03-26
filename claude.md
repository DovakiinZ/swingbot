# ██████████████████████████████████████████████████████
# SWINGBOT — GAME CHANGER PROMPT
# The complete transformation from a basic scanner bot
# into a 24/7 AI-powered compounding trading machine.
# ██████████████████████████████████████████████████████

> READ THIS ENTIRE DOCUMENT BEFORE WRITING A SINGLE LINE OF CODE.
> Every decision — architectural, strategic, and technical — must
> be consistent with the philosophy in INVESTOR_MINDSET.md.
> Read that file first. Then come back here.

---

## THE MISSION

Transform swingbot from a cautious 2-hour scan bot running on
Binance only into a **24/7 AI-powered opportunity hunter** that:

- Watches crypto markets every 10 minutes, around the clock
- Trades both long AND short
- Runs on **Bybit** as the primary exchange (Gulf region compatible)
- Compounds position sizes automatically as the account grows
- Detects breakout setups, not just RSI/EMA crossovers
- Uses a **Random Forest model** (100+ factors voting simultaneously)
  to replace gut-feeling signals with mathematical probability
- Serves a **mobile-first dashboard** accessible from anywhere on
  your phone, hosted free on Oracle Cloud — running 24/7

**The goal: $100 → $1000 in ~100 trades through disciplined
compounding and mathematically sound entry decisions.**

---

## EXISTING CODEBASE STRUCTURE

```
swingbot/
├── core/
│   ├── types.py          # Signal, Position, Order, Side, Reason dataclasses
│   ├── clock.py          # Clock for paper/live mode
│   ├── utils.py          # Logging setup, helpers
│   └── i18n.py           # Arabic/English localization
├── data/
│   ├── market.py         # MarketData — fetches OHLCV via ccxt
│   ├── features.py       # FeatureEngine — computes RSI, EMA, MACD, BBands, ATR, ADX
│   ├── sentiment.py      # Fear & Greed index
│   └── polymarket_client.py  # Macro probability (NOT used for trading — haram)
├── strategy/
│   ├── rsi_ema.py        # Current strategy — LONG only, RSI+EMA
│   ├── scanner.py        # Scores symbols 0-100 (current MIN_SCORE = 55)
│   ├── regimes.py        # TRENDING / RANGING / HIGH_VOLATILITY / UNCERTAIN
│   ├── selector.py       # Picks top N symbols by volume
│   └── macro_filter.py   # Scales risk based on macro conditions
├── risk/
│   ├── risk_engine.py    # Position sizing, portfolio checks
│   └── circuit_breakers.py  # Daily loss limit, consecutive loss limit
├── execution/
│   ├── broker_base.py    # Abstract broker interface
│   ├── broker_binance.py # Binance live broker
│   └── broker_paper.py   # Paper trading broker (simulated)
├── optimize/
│   ├── bandit.py         # Multi-armed bandit (Thompson Sampling)
│   └── param_sets.py     # 8 strategy parameter sets (arms)
├── storage/
│   ├── sqlite_store.py   # All persistence — trades, positions, stats
│   └── schema.sql        # Database schema
├── dashboard/
│   ├── routes.py         # Flask routes (basic, needs full rebuild)
│   ├── state.py          # Shared state dict between bot and dashboard
│   └── templates/
│       └── index.html    # Current dashboard (needs full rebuild)
├── reports/
│   └── daily_report.py   # Generates daily JSON report
├── signals/
│   └── dump_btc.py       # BTC dump risk factor
├── run.py                # MAIN ENTRY POINT — 2-hour scan loop
├── config.yaml           # All configuration
├── requirements.txt      # Python dependencies
└── INVESTOR_MINDSET.md   # THE BOT'S SOUL — read this first
```

---

## IMPLEMENTATION PLAN

This is structured in three phases. Implement them in order.
Do not skip ahead. Each phase builds on the previous one.

```
PHASE 1 — ENGINE REBUILD    (Tasks 1–8)   Core trading improvements
PHASE 2 — DASHBOARD & CLOUD (Tasks 9–12)  Mobile UI + free hosting
PHASE 3 — AI BRAIN          (Tasks 13–17) Random Forest intelligence
```

---

# ═══════════════════════════════════════════════════════
# PHASE 1 — ENGINE REBUILD
# Make the bot fast, aggressive, and multi-directional
# ═══════════════════════════════════════════════════════

---

### TASK 1 — Replace the 2-hour scan cycle in `run.py`

The current bot uses `schedule.every(2).hours.do(job)`.
This is far too slow for opportunity hunting. Replace it entirely.

**New main loop:**
```python
while True:
    job()
    interval = CONFIG.get('scan_interval_minutes', 10) * 60
    time.sleep(interval)
```

**Add `--fast` CLI flag** for testing (2-minute interval):
```python
parser.add_argument('--fast', action='store_true',
    help='Run scan every 2 minutes (testing only)')

# In main():
if args.fast:
    CONFIG['scan_interval_minutes'] = 2
```

**Add `--interval` CLI flag** for custom intervals:
```python
parser.add_argument('--interval', type=int, default=None,
    help='Scan interval in minutes (overrides config)')
```

Remove all `schedule` imports — they are no longer needed.

---

### TASK 2 — Compounding position sizing in `risk/risk_engine.py`

The current system uses a fixed `risk_per_trade_percent` forever.
Replace with dynamic compounding that grows with the account.

**Add this method to `RiskEngine`:**

```python
def get_dynamic_risk_percent(
    self,
    current_balance: float,
    base_balance: float,
    setup_score: float,
    peak_balance: float
) -> float:
    """
    Dynamic risk % based on account growth phase and setup quality.

    Growth phases (from INVESTOR_MINDSET.md compounding plan):
      Phase 1: balance < 2.5x base  → 3.0% base risk
      Phase 2: balance < 5.0x base  → 3.5% base risk
      Phase 3: balance >= 5.0x base → 4.0% base risk

    Setup score multiplier:
      score >= 80 → 1.5x  (high conviction — size up)
      score >= 65 → 1.0x  (standard)
      score <  65 → 0.75x (low conviction — size down)

    Drawdown protection:
      If drawdown from peak > 20% → reset to Phase 1 risk (3.0%)

    Hard cap: never exceed 5.0% of current balance on any single trade.
    """
    # Drawdown check
    if peak_balance > 0:
        drawdown = (peak_balance - current_balance) / peak_balance
        if drawdown > 0.20:
            base_risk = 3.0  # Reset to Phase 1
        elif current_balance >= base_balance * 5.0:
            base_risk = 4.0  # Phase 3
        elif current_balance >= base_balance * 2.5:
            base_risk = 3.5  # Phase 2
        else:
            base_risk = 3.0  # Phase 1
    else:
        base_risk = 3.0

    # Score multiplier
    if setup_score >= 80:
        multiplier = 1.5
    elif setup_score >= 65:
        multiplier = 1.0
    else:
        multiplier = 0.75

    risk_pct = base_risk * multiplier
    return min(risk_pct, 5.0)  # Hard cap at 5%
```

**Update `calculate_position_size()`** to call `get_dynamic_risk_percent()`
instead of using the fixed `self.risk_per_trade_percent`.

**Track peak balance in SQLite:**
Add `peak_balance REAL DEFAULT 0` to the daily stats table in `schema.sql`.
In `sqlite_store.py`, add `get_peak_balance()` and `update_peak_balance()`
methods. Update peak on every cycle in `run.py` after fetching balance.

---

### TASK 3 — Breakout detection in `strategy/scanner.py`

The current scanner misses the most explosive moves — breakouts from
consolidation. Add breakout detection as a first-class signal.

**Add Breakout Setup scoring (25 pts):**

```python
def _score_breakout(self, df: pd.DataFrame) -> tuple[float, bool]:
    """
    Detect compression → expansion breakout pattern.
    Returns (score, breakout_detected_flag).

    Breakout conditions (ALL must be true):
    1. Previous N candles had ATR% < 2.0% (compression phase)
    2. Current volume >= 2.0x the 20-period volume MA (surge)
    3. Price broke above highest high OR below lowest low of last N candles
       by at least 0.1% margin

    Returns 25 pts and True if breakout detected, else 0 pts and False.
    """
    lookback = 20
    if len(df) < lookback + 1:
        return 0.0, False

    recent = df.iloc[-(lookback+1):-1]  # Previous N candles
    curr   = df.iloc[-1]

    # 1. Compression check
    avg_atr_pct = recent['atr_percent'].mean()
    if avg_atr_pct >= 2.0:
        return 0.0, False

    # 2. Volume surge
    vol_ratio = curr.get('volume_ratio', 1.0)
    if vol_ratio < 2.0:
        return 0.0, False

    # 3. Price breakout
    highest_high = recent['high'].max()
    lowest_low   = recent['low'].min()
    close        = curr['close']

    broke_up   = close > highest_high * 1.001   # 0.1% margin above
    broke_down = close < lowest_low  * 0.999   # 0.1% margin below

    if broke_up or broke_down:
        return 25.0, True

    return 0.0, False
```

**Integrate into `score_symbol()`:**
- Call `_score_breakout()` and add its pts to the total score
- Return `breakout_detected` as part of the result (add it to `ScanResult`)
- Reduce ADX scoring from 15pts → 10pts (total remains 100pts)
- Raise `MIN_SCORE` from 55 → **65**

**Update `ScanResult` in `core/types.py`:**
```python
@dataclass
class ScanResult:
    symbol: str
    score: float
    rsi: float
    atr_pct: float
    volume_rank: int
    trend: str
    regime: str
    scanned_at: int
    breakout_detected: bool = False   # NEW
```

In `run.py`, when `breakout_detected` is True, apply **1.5x position size multiplier**.

---

### TASK 4 — Short/SELL signals in `strategy/rsi_ema.py`

The bot is currently long-only. This means it sits idle in downtrends.
Add short entry and exit logic.

**Update `check_signal()` signature:**
```python
def check_signal(
    self,
    df: pd.DataFrame,
    regime: MarketRegime,
    params: StrategyParams,
    current_position=None,   # Pass actual Position object, not bool
    symbol: str = "BTC/USDT"
) -> Optional[Signal]:
```

**Short entry logic (add after long entry block):**
```python
# ── Short Entry (bearish trend + overbought) ──────────────────────────
short_trend_ok = ema_fast < ema_slow          # Confirmed downtrend
short_rsi_ok   = rsi > params.rsi_exit        # Overbought
short_vol_ok   = atr_pct < 5.0               # Not chaotic

if short_trend_ok and short_rsi_ok and short_vol_ok:
    return Signal(
        symbol=symbol,
        side=Side.SELL,
        reason=Reason.SIGNAL_ENTRY,
        price=close,
        stop_loss=close + (atr * params.sl_mult),    # SL above price
        take_profit=close - (atr * params.tp_mult),  # TP below price
        params=params
    )
```

**Short exit logic (cover/close short):**
```python
# ── Short Exit — cover when oversold or trend flips ──────────────────
if current_side == Side.SELL:
    if rsi < params.rsi_entry:
        return Signal(symbol=symbol, side=Side.BUY,
                      reason=Reason.RSI_EXIT, price=close,
                      stop_loss=0, take_profit=0)
    if ema_fast > ema_slow:
        return Signal(symbol=symbol, side=Side.BUY,
                      reason=Reason.TREND_FLIP, price=close,
                      stop_loss=0, take_profit=0)
```

**In `run.py`**, update all calls to `check_signal()` — pass the `pos`
object instead of `True` for the `current_position` argument.

**Only allow shorts if `config.yaml` has `allow_short: true`.**
Check this flag before generating short signals.

---

### TASK 5 — Bybit as primary exchange in `execution/`

Bybit works in the Gulf region. Binance may not. Bybit is now primary.

**5a — Create `execution/broker_bybit.py`:**

```python
"""
Bybit broker — primary live trading broker for swingbot.
Uses Bybit Unified Trading Account (UTA) via ccxt.
Supports both spot and linear futures via category param.
Bybit v5 API requires 'category' on all order endpoints.
"""
import os
import logging
import ccxt
from typing import List, Optional
from core.types import Signal, Order, Position, Side, OrderType, OrderStatus, PositionStatus, Reason
from execution.broker_base import Broker
from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

class BybitBroker(Broker):
    def __init__(self, store: SQLiteStore, market, account_type: str = 'spot'):
        """
        account_type: 'spot' for spot trading, 'linear' for USDT perpetual futures
        """
        self.store = store
        self.market = market
        self.account_type = account_type  # 'spot' or 'linear'

        self.exchange = ccxt.bybit({
            'apiKey': os.getenv('BYBIT_API_KEY'),
            'secret': os.getenv('BYBIT_API_SECRET'),
            'enableRateLimit': True,
            'options': {
                'defaultType': account_type,
                'accountType': 'UNIFIED',   # Bybit Unified Trading Account
            }
        })

    def get_balance(self) -> float:
        """Returns available USDT balance."""
        try:
            balance = self.exchange.fetch_balance()
            return float(balance['USDT']['free'] or 0)
        except Exception as e:
            logger.error(f"[Bybit] Balance fetch failed: {e}")
            return 0.0

    def get_detailed_balance(self) -> dict:
        try:
            balance = self.exchange.fetch_balance()
            return {
                'USDT_free':  float(balance['USDT'].get('free', 0) or 0),
                'USDT_total': float(balance['USDT'].get('total', 0) or 0),
            }
        except Exception as e:
            logger.error(f"[Bybit] Detailed balance failed: {e}")
            return {'USDT_free': 0, 'USDT_total': 0}

    def place_order(self, signal: Signal, size: float) -> Optional[Order]:
        """
        Place a market order on Bybit.
        Bybit v5 API requires 'category' parameter.
        """
        try:
            side = 'buy' if signal.side == Side.BUY else 'sell'
            order = self.exchange.create_order(
                symbol=signal.symbol,
                type='market',
                side=side,
                amount=size,
                params={'category': self.account_type}
            )
            logger.warning(
                f"[Bybit] Order placed: {side.upper()} {size} {signal.symbol} "
                f"@ ~{signal.price:.4f} | ID: {order['id']}"
            )
            # Save and return order object
            result = Order(
                id=order['id'],
                symbol=signal.symbol,
                side=signal.side,
                order_type=OrderType.MARKET,
                amount=size,
                price=signal.price,
                status=OrderStatus.FILLED,
                filled_amount=size,
                filled_price=float(order.get('average', signal.price) or signal.price),
                timestamp=int(order.get('timestamp', 0) or 0)
            )
            self.store.save_order(result)
            return result
        except Exception as e:
            logger.error(f"[Bybit] Order failed: {e}")
            return None

    def get_open_positions(self) -> List[Position]:
        return self.store.get_open_positions()

    def get_open_orders(self) -> list:
        try:
            return self.exchange.fetch_open_orders(
                params={'category': self.account_type}
            )
        except Exception as e:
            logger.error(f"[Bybit] Open orders fetch failed: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.exchange.cancel_order(
                order_id,
                params={'category': self.account_type}
            )
            return True
        except Exception as e:
            logger.error(f"[Bybit] Cancel order failed: {e}")
            return False

    def sync(self):
        pass
```

**5b — Update `run.py` broker selection:**
```python
from execution.broker_bybit import BybitBroker

primary_exchange = CONFIG.get('primary_exchange', 'bybit')

if is_live:
    if primary_exchange == 'bybit':
        account_type = CONFIG.get('bybit_account_type', 'spot')
        broker = BybitBroker(store, market, account_type=account_type)
    elif primary_exchange == 'binance':
        broker = BinanceBroker(store, market)
    else:
        raise ValueError(f"Unknown exchange: {primary_exchange}")
else:
    broker = PaperBroker(store, clock, initial_balance=init_bal)
```

**5c — Update `data/market.py`** default exchange to bybit:
```python
class MarketData:
    def __init__(self, exchange_id: str = 'bybit', sandbox: bool = False):
```

**5d — Update `.env.example`:**
```
# ── Bybit (Primary — Gulf region supported) ───────────────────────────
# bybit.com → API Management → Create New Key
# Permissions: Read + Trade ONLY. NEVER enable Withdrawal.
BYBIT_API_KEY=
BYBIT_API_SECRET=

# ── Binance (Optional secondary) ─────────────────────────────────────
BINANCE_API_KEY=
BINANCE_API_SECRET=

# ── Dashboard ─────────────────────────────────────────────────────────
DASHBOARD_PASSWORD=changeme123
FLASK_SECRET_KEY=replace-with-a-long-random-string
```

**5e — Update `config.yaml`:**
```yaml
primary_exchange: bybit        # 'bybit' (recommended) or 'binance'
bybit_account_type: spot       # 'spot' or 'linear' (futures)
```

---

### TASK 6 — Trailing stop-loss in `execution/broker_paper.py`

Lock in profits as price moves in our favor. Never give back gains.

**Add `update_trailing_stop()` to `PaperBroker`:**

```python
def update_trailing_stop(self, symbol: str, candle_high: float,
                          candle_low: float, trail_atr: float) -> None:
    """
    Ratchet the stop-loss in the direction of profit.
    For LONG:  new_sl = candle_high - trail_atr → move up if > current SL
    For SHORT: new_sl = candle_low  + trail_atr → move down if < current SL
    Stop only ever moves in the direction of profit — never against it.
    Activates only after position is at least 1R in profit.
    """
    pos = self._positions.get(symbol)
    if not pos:
        return

    if pos.side == Side.BUY:
        # Only trail after 1R profit
        r = abs(pos.entry_price - pos.stop_loss)
        if candle_high < pos.entry_price + r:
            return   # Not yet 1R in profit
        new_sl = candle_high - trail_atr
        if new_sl > pos.stop_loss:
            pos.stop_loss = new_sl
            self.store.save_position(pos)

    elif pos.side == Side.SELL:
        r = abs(pos.entry_price - pos.stop_loss)
        if candle_low > pos.entry_price - r:
            return   # Not yet 1R in profit
        new_sl = candle_low + trail_atr
        if new_sl < pos.stop_loss:
            pos.stop_loss = new_sl
            self.store.save_position(pos)
```

**Call it inside `check_sl_tp()`** before evaluating SL/TP hits:
```python
# Ratchet trailing stop before checking hits
trail_atr = candle.atr * 1.5 if hasattr(candle, 'atr') else 0
self.update_trailing_stop(pos.symbol, candle.high, candle.low, trail_atr)
```

Also apply the same trailing logic to `broker_bybit.py` for live trading,
using the position's stored stop-loss and updating it via an exchange
stop-loss order modification if supported, or tracking it internally.

---

### TASK 7 — Entry checklist gate in `run.py`

Every single trade must pass a mandatory pre-flight checklist.
No exceptions. No shortcuts. Log the reason for every skip.

```python
def _passes_entry_checklist(
    macro_scale: float,
    sentiment_ok: bool,
    score: float,
    signal,
    circuit_breaker_ok: bool
) -> tuple[bool, str]:
    """
    Pre-flight checklist before any entry order.
    From INVESTOR_MINDSET.md — all 7 conditions must be YES.
    Returns (passed, reason_if_failed).
    """
    if not circuit_breaker_ok:
        return False, "Circuit breaker tripped"
    if macro_scale < 0.5:
        return False, f"Macro risk too high (scale={macro_scale:.2f})"
    if not sentiment_ok:
        return False, "Extreme fear — sentiment gate blocked"
    if score < CONFIG.get('min_score', 65):
        return False, f"Score too low ({score:.0f} < {CONFIG.get('min_score', 65)})"
    if signal is None:
        return False, "No signal generated"
    if signal.stop_loss and signal.price:
        sl_dist = abs(signal.price - signal.stop_loss)
        tp_dist = abs(signal.price - (signal.take_profit or 0))
        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        min_rr = CONFIG.get('min_rr_ratio', 2.0)
        if rr < min_rr:
            return False, f"R:R too low ({rr:.2f} < {min_rr})"
    return True, "OK"
```

Call this in the entry loop before `broker.place_order()`.
Log every failure with `logger.warning(f"[SKIP] {sym}: {reason}")`.

---

### TASK 8 — Update `config.yaml` with all new parameters

Add these sections to the existing `config.yaml`:

```yaml
# ── Scan Speed ────────────────────────────────────────────────────────
scan_interval_minutes: 10        # How often to scan (minutes)

# ── Exchange ──────────────────────────────────────────────────────────
primary_exchange: bybit          # 'bybit' or 'binance'
bybit_account_type: spot         # 'spot' or 'linear' (futures)

# ── Compounding Plan ($100 → $1000) ──────────────────────────────────
base_balance: 100.0              # Starting capital reference
peak_balance_tracking: true      # Track peak for drawdown calculation
drawdown_reset_threshold: 0.20   # Reset to Phase 1 if drawdown > 20%

# ── Breakout Detection ────────────────────────────────────────────────
breakout_lookback: 20            # Candles to look back
breakout_volume_mult: 2.0        # Min volume surge multiplier
breakout_compression_atr: 2.0    # Max ATR% during consolidation

# ── Signal Quality Gates ──────────────────────────────────────────────
allow_short: true                # Enable short/sell entries
min_score: 65                    # Minimum scanner score (was 55)
min_rr_ratio: 2.0                # Minimum Risk:Reward ratio

# ── Dashboard ─────────────────────────────────────────────────────────
dashboard:
  enabled: true
  port: 8080
  host: '0.0.0.0'
```

---

# ═══════════════════════════════════════════════════════
# PHASE 2 — DASHBOARD & CLOUD
# Mobile-first interface + free Oracle Cloud hosting
# ═══════════════════════════════════════════════════════

---

### TASK 9 — Mobile-first dashboard in `dashboard/`

Rebuild `dashboard/templates/index.html` from scratch.
Single HTML file. Inline CSS + vanilla JS. CDN Chart.js only.
No npm. No build step. No frameworks.

**Design spec:**
- Dark theme (`#0d0d0d` background, `#1a1a2e` cards)
- Accent colors: green `#00ff88`, red `#ff4757`, gold `#ffd700`
- Font: system-ui / -apple-system (no Google Fonts — works offline)
- Min width: 375px (iPhone SE). Max width: 600px centered on desktop.
- Three tabs at bottom (like a mobile app): Status | Positions | History
- Auto-refresh every 30 seconds via `setInterval` + `fetch('/api/status')`
- Show a "🔴 Disconnected" banner if fetch fails

**Tab 1 — Status (home screen):**
```
┌──────────────────────────────┐
│  SWINGBOT          🟢 PAPER  │
│                              │
│         $247.83              │  ← big balance
│    Day P&L: +$12.40 +5.3%   │  ← green/red
│                              │
│  ⚡ Circuit Breaker   OK     │
│  😐 Sentiment        Neutral │
│  📊 Macro Scale      0.85    │
│  🤖 AI Confidence    --      │
│  ⏱  Next Scan        8m 32s  │
│                              │
│  ── Top Opportunities ───── │
│  BTC/USDT    87/100  🔥 BUY  │
│  ETH/USDT    72/100     BUY  │
│  SOL/USDT    66/100     BUY  │
└──────────────────────────────┘
```

**Tab 2 — Positions:**
Each open position = one card:
```
┌──────────────────────────────┐
│ BTC/USDT           🟢 LONG   │
│                              │
│ Entry    $43,250.00          │
│ P&L      +$8.42  (+1.9%)    │ ← green if +, red if -
│ Stop     $42,100.00          │
│ Target   $45,800.00          │
│ Opened   2h 14m ago          │
│ Score    87/100              │
└──────────────────────────────┘
```
Green left border if profitable. Red left border if losing.
"No open positions" empty state with moon emoji if flat.

**Tab 3 — History:**
- Balance chart (Chart.js line, last 30 trades, green line on dark)
- Stats row: Win Rate | Total Trades | Best Trade | Sharpe
- Win streak: "🔥 3 wins in a row" or "❄️ 2 losses in a row"
- Scrollable trade list (last 20):
  `BTC/USDT  LONG  +$4.21  TP ✅`
  `ETH/USDT  SHORT -$1.80  SL ❌`

**Update `dashboard/routes.py`** with these API endpoints:

```python
@app.route('/api/status')
@login_required
def api_status():
    return jsonify({
        'balance': float,
        'mode': 'paper' | 'live',
        'day_pnl': float,
        'day_pnl_pct': float,
        'circuit_breaker': str,
        'sentiment_ok': bool,
        'macro_scale': float,
        'ai_confidence': float | None,   # Phase 3
        'next_scan_seconds': int,
        'scan_results': [{'symbol': str, 'score': float, 'signal': str}],
        'open_positions_count': int,
        'last_updated': str   # ISO timestamp
    })

@app.route('/api/positions')
@login_required
def api_positions():
    return jsonify([{
        'symbol': str,
        'side': 'LONG' | 'SHORT',
        'entry_price': float,
        'current_price': float,
        'unrealized_pnl': float,
        'unrealized_pnl_pct': float,
        'stop_loss': float,
        'take_profit': float,
        'score': float,
        'opened_ago_seconds': int
    }])

@app.route('/api/history')
@login_required
def api_history():
    return jsonify({
        'trades': [{'symbol', 'side', 'pnl', 'pnl_pct', 'reason', 'closed_at'}],
        'balance_history': [float],   # ordered list of balances after each trade
        'win_streak': int,            # positive = wins, negative = losses
        'win_rate': float,
        'sharpe_ratio': float | None,
        'total_trades': int
    })
```

---

### TASK 10 — Password protection for the dashboard

Single-password session login. No database. No user accounts.
Password set via environment variable.

```python
import os
from functools import wraps
from flask import session, request, redirect, render_template_string

def create_app(config: dict = None):
    app = Flask(__name__)
    app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-change-me')

    DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', 'swingbot123')

    LOGIN_HTML = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Swingbot Login</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { background: #0d0d0d; color: #fff; font-family: system-ui;
                   display: flex; align-items: center; justify-content: center;
                   min-height: 100vh; }
            .card { background: #1a1a2e; border-radius: 16px; padding: 40px 32px;
                    width: 100%; max-width: 360px; margin: 20px; }
            h1 { font-size: 24px; margin-bottom: 8px; }
            p { color: #888; font-size: 14px; margin-bottom: 32px; }
            input { width: 100%; padding: 14px 16px; background: #0d0d0d;
                    border: 1px solid #333; border-radius: 10px; color: #fff;
                    font-size: 16px; margin-bottom: 16px; }
            button { width: 100%; padding: 14px; background: #00ff88;
                     color: #000; border: none; border-radius: 10px;
                     font-size: 16px; font-weight: 700; cursor: pointer; }
            .error { color: #ff4757; font-size: 14px; margin-bottom: 16px; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>⚡ Swingbot</h1>
            <p>Enter your dashboard password</p>
            {% if error %}<div class="error">{{ error }}</div>{% endif %}
            <form method="POST">
                <input type="password" name="password"
                       placeholder="Password" autofocus>
                <button type="submit">Enter Dashboard</button>
            </form>
        </div>
    </body>
    </html>
    """

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('logged_in'):
                return redirect('/login')
            return f(*args, **kwargs)
        return decorated

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            if request.form.get('password') == DASHBOARD_PASSWORD:
                session['logged_in'] = True
                return redirect('/')
            error = 'Wrong password'
        return render_template_string(LOGIN_HTML, error=error)

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect('/login')

    # Apply login_required to all other routes...
    return app
```

---

### TASK 11 — Run bot + dashboard as one process in `run.py`

```python
import threading
from dashboard.routes import create_app

def start_dashboard(config: dict) -> None:
    """Start Flask dashboard in a background daemon thread."""
    if not config.get('dashboard', {}).get('enabled', True):
        return
    app  = create_app(config)
    port = config.get('dashboard', {}).get('port', 8080)
    host = config.get('dashboard', {}).get('host', '0.0.0.0')

    t = threading.Thread(
        target=lambda: app.run(host=host, port=port,
                               debug=False, use_reloader=False),
        daemon=True,
        name='dashboard'
    )
    t.start()
    logger.warning(f"[Dashboard] Running at http://{host}:{port}")
```

Call `start_dashboard(CONFIG)` at the top of `main()`, before the
trading loop starts. The daemon thread dies automatically when the
main process exits — no cleanup needed.

Pass `dashboard_state` dict (already exists in `run.py`) into
`create_app()` so routes can read live bot state without a database
round-trip for the most time-sensitive fields (next_scan countdown etc).

---

### TASK 12 — Deployment files for Oracle Cloud (free forever)

**`Dockerfile`:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App
COPY . .
RUN mkdir -p logs data

EXPOSE 8080

# Health check
HEALTHCHECK --interval=60s --timeout=10s \
    CMD curl -f http://localhost:8080/login || exit 1

CMD ["python", "run.py", "--lang", "en"]
```

**`docker-compose.yml`:**
```yaml
version: '3.8'

services:
  swingbot:
    build: .
    container_name: swingbot
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data       # SQLite database persists here
      - ./logs:/app/logs       # Logs persist here
    env_file:
      - .env
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

**`start.sh`** (no Docker — bare Ubuntu VM):
```bash
#!/bin/bash
# Swingbot startup script for Oracle Cloud Ubuntu VM
set -e
cd "$(dirname "$0")"

echo "Installing dependencies..."
pip install -r requirements.txt --quiet

echo "Creating directories..."
mkdir -p logs data

echo "Starting Swingbot..."
nohup python run.py --lang en > logs/output.log 2>&1 &
BOT_PID=$!

echo ""
echo "✅ Swingbot started (PID: $BOT_PID)"
echo "📱 Dashboard: http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_IP'):8080"
echo "📋 Logs: tail -f logs/output.log"
echo "🛑 Stop: kill $BOT_PID"
echo ""
echo $BOT_PID > .bot.pid
```

**`stop.sh`:**
```bash
#!/bin/bash
if [ -f .bot.pid ]; then
    kill $(cat .bot.pid) && rm .bot.pid
    echo "Swingbot stopped."
else
    echo "No PID file found. Try: pkill -f run.py"
fi
```

**`DEPLOY.md`:**
```markdown
# Deploy Swingbot on Oracle Cloud — Free Forever

## Step 1 — Create Oracle Cloud Account
1. Go to https://cloud.oracle.com
2. Click "Start for free"
3. Choose Always Free tier
4. Enter credit card (NEVER charged unless you manually upgrade)

## Step 2 — Create Your Free VM
1. Compute → Instances → Create Instance
2. Name: swingbot
3. Image: Ubuntu 22.04
4. Shape: VM.Standard.A1.Flex (ARM) → Always Free ✓
   - Set OCPU: 2, RAM: 12GB (well within free limits)
5. Add SSH Key: paste your public key or download a new one
6. Click Create

## Step 3 — Open Port 8080 (for dashboard)
1. Click your instance → Subnet → Security List
2. Add Ingress Rule:
   - Source: 0.0.0.0/0
   - Protocol: TCP
   - Port: 8080
3. Also run on the VM:
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8080 -j ACCEPT
   sudo netfilter-persistent save

## Step 4 — Deploy the Bot
```bash
# SSH into your VM
ssh ubuntu@YOUR_VM_IP

# Install Python
sudo apt update && sudo apt install -y python3-pip git

# Clone your repo
git clone https://github.com/YOUR_USERNAME/swingbot.git
cd swingbot

# Configure
cp .env.example .env
nano .env
# Fill in:
#   BYBIT_API_KEY=
#   BYBIT_API_SECRET=
#   DASHBOARD_PASSWORD=your_secret_password
#   FLASK_SECRET_KEY=any_long_random_string

# Start
bash start.sh
```

## Step 5 — Access on Your Phone
Open in mobile browser:
http://YOUR_VM_IP:8080

Bookmark it. Add to home screen for app-like experience.

## Step 6 — Auto-restart on VM reboot (optional)
```bash
# Create systemd service
sudo nano /etc/systemd/system/swingbot.service
```
```ini
[Unit]
Description=Swingbot Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/swingbot
ExecStart=/usr/bin/python3 run.py --lang en
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable swingbot
sudo systemctl start swingbot
```
Bot now auto-starts on every VM reboot.
```

---

# ═══════════════════════════════════════════════════════
# PHASE 3 — AI BRAIN
# Random Forest model replaces gut-feeling signals
# 100+ factors voting simultaneously
# ═══════════════════════════════════════════════════════

> IMPORTANT: Phase 3 is implemented AFTER the bot has collected
> at least 50-100 real paper trades in Phase 1 & 2. The model
> needs real trade outcomes as training data. Do not skip ahead.
> The code in Phase 3 should be built now but the model will
> only be trained after sufficient data is collected.

---

### TASK 13 — Trade data collection for AI training in `storage/`

Every closed trade must save a rich feature snapshot — the exact
market conditions at entry time. This is the training data for the
Random Forest model.

**Add to `schema.sql`:**
```sql
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
    ema_fast_slope  REAL,   -- EMA direction: (current - prev) / prev
    ema_slow_slope  REAL,
    adx             REAL,
    -- Volatility features
    atr             REAL,
    atr_percent     REAL,
    bb_position     REAL,   -- (close - bb_lower) / (bb_upper - bb_lower)
    bb_width        REAL,   -- (bb_upper - bb_lower) / bb_mid
    -- Volume features
    volume_ratio    REAL,
    volume_24h      REAL,
    -- Setup quality features
    scanner_score   REAL,
    breakout_detected INTEGER,  -- 0 or 1
    regime          TEXT,
    -- Macro features
    fear_greed      REAL,
    macro_scale     REAL,
    btc_dominance   REAL,
    -- Market context
    hour_of_day     INTEGER,    -- 0-23 UTC
    day_of_week     INTEGER,    -- 0=Monday
    -- Outcome (filled when trade closes)
    outcome         INTEGER,    -- 1 = win, 0 = loss
    pnl             REAL,
    pnl_percent     REAL,
    hold_hours      REAL,
    exit_reason     TEXT,
    -- Timestamps
    captured_at     INTEGER NOT NULL
);
```

**Add to `sqlite_store.py`:**
```python
def save_trade_features(self, features: dict) -> None:
    """Save feature snapshot at trade entry for AI training."""

def get_training_data(self, min_samples: int = 50) -> Optional[pd.DataFrame]:
    """
    Returns DataFrame of completed trades with features and outcomes.
    Returns None if insufficient samples.
    """

def get_training_data_count(self) -> int:
    """Returns number of completed labeled training samples."""
```

**In `run.py`**, when a BUY order is placed, immediately capture and
save all current feature values to `trade_features` table.
When the trade closes, update `outcome`, `pnl`, `pnl_percent`,
`hold_hours`, and `exit_reason` for that record.

---

### TASK 14 — Feature engineering for AI in `data/features.py`

Add a method that extracts the full feature vector for a single
candle — used both for saving training data and for live inference.

```python
@staticmethod
def extract_ml_features(df: pd.DataFrame,
                         scanner_score: float = 0,
                         breakout_detected: bool = False,
                         macro_scale: float = 1.0,
                         fear_greed: float = 50.0) -> dict:
    """
    Extract the full feature vector for ML inference.
    Returns a flat dict matching the trade_features schema.
    All features are normalized/cleaned (no NaN, no inf).
    """
    curr = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else curr

    def safe(val, default=0.0):
        if val is None or (isinstance(val, float) and (pd.isna(val) or not pd.isfinite(val))):
            return default
        return float(val)

    close    = safe(curr['close'])
    bb_upper = safe(curr.get('bb_upper', 0))
    bb_lower = safe(curr.get('bb_lower', 0))
    bb_mid   = safe(curr.get('bb_mid', close))
    bb_range = (bb_upper - bb_lower) if bb_upper > bb_lower else 1

    ema_fast_curr = safe(curr.get('ema_fast', close))
    ema_fast_prev = safe(prev.get('ema_fast', ema_fast_curr))
    ema_slow_curr = safe(curr.get('ema_slow', close))
    ema_slow_prev = safe(prev.get('ema_slow', ema_slow_curr))

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    return {
        'price':           close,
        'rsi_14':          safe(curr.get('rsi', 50)),
        'rsi_7':           safe(curr.get('rsi_7', 50)),
        'macd':            safe(curr.get('macd', 0)),
        'macd_signal':     safe(curr.get('macd_signal', 0)),
        'macd_hist':       safe(curr.get('macd_hist', 0)),
        'ema_fast':        ema_fast_curr,
        'ema_slow':        ema_slow_curr,
        'ema_fast_slope':  (ema_fast_curr - ema_fast_prev) / max(ema_fast_prev, 1),
        'ema_slow_slope':  (ema_slow_curr - ema_slow_prev) / max(ema_slow_prev, 1),
        'adx':             safe(curr.get('adx', 0)),
        'atr':             safe(curr.get('atr', 0)),
        'atr_percent':     safe(curr.get('atr_percent', 0)),
        'bb_position':     (close - bb_lower) / bb_range,
        'bb_width':        bb_range / max(bb_mid, 1),
        'volume_ratio':    safe(curr.get('volume_ratio', 1)),
        'scanner_score':   scanner_score,
        'breakout_detected': 1 if breakout_detected else 0,
        'macro_scale':     macro_scale,
        'fear_greed':      fear_greed,
        'hour_of_day':     now.hour,
        'day_of_week':     now.weekday(),
    }
```

Also add `rsi_7` to `compute_indicators()` in `FeatureEngine`:
```python
df['rsi_7'] = ta.momentum.RSIIndicator(close=df['close'], window=7).rsi()
```

---

### TASK 15 — Random Forest model in `ml/model.py` (new module)

Create a new `ml/` directory with `__init__.py` and `model.py`.

```python
"""
ml/model.py — Random Forest trading signal model.

Architecture (from the article's technique):
- 100+ decision trees (n_estimators=200 for robustness)
- Each tree sees sqrt(n_features) random features — diverse perspectives
- Final prediction = majority vote across all trees (probability 0.0-1.0)
- Only enter when model confidence >= 0.70 (70%+ of trees agree)
- Calibrated with Platt scaling for reliable probability estimates

Entry gate formula (from the article):
  if market_price <= model_probability * 0.5: buy()
  Applied as: only enter if setup score implies 2x value vs risk

This is NOT Polymarket. This is the TECHNIQUE applied to crypto OHLCV data.
"""

import logging
import pickle
from pathlib import Path
from typing import Optional, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Features used by the model (must match trade_features schema)
FEATURE_COLUMNS = [
    'rsi_14', 'rsi_7', 'macd', 'macd_signal', 'macd_hist',
    'ema_fast_slope', 'ema_slow_slope', 'adx',
    'atr_percent', 'bb_position', 'bb_width',
    'volume_ratio', 'scanner_score', 'breakout_detected',
    'macro_scale', 'fear_greed', 'hour_of_day', 'day_of_week'
]

MODEL_PATH = Path('data/swingbot_model.pkl')
MIN_TRAINING_SAMPLES = 50
CONFIDENCE_THRESHOLD = 0.70   # Only trade when 70%+ confident


class SwingbotModel:
    def __init__(self):
        self.model = None
        self.is_trained = False
        self._load_if_exists()

    def _load_if_exists(self) -> None:
        """Load pre-trained model from disk if available."""
        if MODEL_PATH.exists():
            try:
                with open(MODEL_PATH, 'rb') as f:
                    self.model = pickle.load(f)
                self.is_trained = True
                logger.info(f"[ML] Model loaded from {MODEL_PATH}")
            except Exception as e:
                logger.warning(f"[ML] Could not load model: {e}")

    def train(self, df: pd.DataFrame) -> dict:
        """
        Train Random Forest on historical trade data.
        df must have FEATURE_COLUMNS + 'outcome' column.
        Returns training metrics dict.
        """
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.calibration import CalibratedClassifierCV
            from sklearn.model_selection import cross_val_score
            from sklearn.metrics import classification_report

            if len(df) < MIN_TRAINING_SAMPLES:
                return {'error': f'Need {MIN_TRAINING_SAMPLES} samples, have {len(df)}'}

            # Prepare data
            X = df[FEATURE_COLUMNS].fillna(0)
            y = df['outcome'].astype(int)

            # Random Forest — 200 trees, sqrt features per tree (article technique)
            n_features_sqrt = int(np.sqrt(len(FEATURE_COLUMNS)))
            rf = RandomForestClassifier(
                n_estimators=200,
                max_features=n_features_sqrt,  # sqrt(n_features) — article spec
                min_samples_leaf=3,
                random_state=42,
                n_jobs=-1,
                class_weight='balanced'
            )

            # Calibrate for reliable probabilities (Platt scaling)
            self.model = CalibratedClassifierCV(rf, cv=5, method='sigmoid')
            self.model.fit(X, y)

            # Cross-validation score
            cv_scores = cross_val_score(self.model, X, y, cv=5, scoring='roc_auc')

            # Feature importance (from underlying RF)
            feat_importance = dict(zip(
                FEATURE_COLUMNS,
                rf.feature_importances_ if hasattr(rf, 'feature_importances_') else [0]*len(FEATURE_COLUMNS)
            ))
            top_features = sorted(feat_importance.items(), key=lambda x: x[1], reverse=True)[:5]

            # Save model
            MODEL_PATH.parent.mkdir(exist_ok=True)
            with open(MODEL_PATH, 'wb') as f:
                pickle.dump(self.model, f)

            self.is_trained = True

            metrics = {
                'samples': len(df),
                'win_rate': float(y.mean()),
                'cv_auc_mean': float(cv_scores.mean()),
                'cv_auc_std': float(cv_scores.std()),
                'top_features': top_features
            }
            logger.warning(f"[ML] Model trained: {metrics}")
            return metrics

        except ImportError:
            return {'error': 'scikit-learn not installed. Run: pip install scikit-learn'}
        except Exception as e:
            logger.error(f"[ML] Training failed: {e}")
            return {'error': str(e)}

    def predict(self, features: dict) -> Tuple[float, bool]:
        """
        Predict win probability for a setup.
        Returns (confidence, should_trade).
        confidence: 0.0-1.0 (probability of winning trade)
        should_trade: True only if confidence >= CONFIDENCE_THRESHOLD

        Entry gate from article:
          confidence >= 0.70 → consider entry
          confidence >= 0.85 → high conviction, 1.5x size
        """
        if not self.is_trained or self.model is None:
            return 0.0, False

        try:
            # Build feature vector in correct order
            row = [features.get(col, 0) or 0 for col in FEATURE_COLUMNS]
            X = pd.DataFrame([row], columns=FEATURE_COLUMNS)
            prob = float(self.model.predict_proba(X)[0][1])   # P(win)
            should_trade = prob >= CONFIDENCE_THRESHOLD
            return prob, should_trade
        except Exception as e:
            logger.error(f"[ML] Prediction failed: {e}")
            return 0.0, False

    def should_enter(self, features: dict, scanner_score: float) -> Tuple[bool, float, str]:
        """
        Full entry gate combining scanner score + model confidence.
        Implements the article's 'money on the floor' principle:
          Only enter when BOTH the scanner AND the model agree.

        Returns (enter, confidence, reason).
        """
        if not self.is_trained:
            # Fall back to scanner-only if model not yet trained
            enter = scanner_score >= 65
            return enter, 0.0, "scanner_only (model not trained yet)"

        confidence, model_ok = self.predict(features)

        if not model_ok:
            return False, confidence, f"model confidence too low ({confidence:.0%})"

        if scanner_score < 65:
            return False, confidence, f"scanner score too low ({scanner_score:.0f})"

        return True, confidence, f"model={confidence:.0%} score={scanner_score:.0f}"

    @property
    def confidence_threshold(self) -> float:
        return CONFIDENCE_THRESHOLD
```

**Add `ml/trainer.py`** — runs training on demand:
```python
"""
ml/trainer.py — Train the model from collected paper trade data.

Usage:
    python -m ml.trainer               # Train on all data
    python -m ml.trainer --min 100     # Require 100+ samples
    python -m ml.trainer --report      # Show current model stats
"""
import argparse
import sys
from storage.sqlite_store import SQLiteStore
from ml.model import SwingbotModel
import yaml

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--min', type=int, default=50)
    parser.add_argument('--report', action='store_true')
    args = parser.parse_args()

    with open('config.yaml') as f:
        config = yaml.safe_load(f)

    store = SQLiteStore(db_path=config['db_path'])
    model = SwingbotModel()

    count = store.get_training_data_count()
    print(f"Training samples available: {count}")

    if args.report:
        print(f"Model trained: {model.is_trained}")
        sys.exit(0)

    if count < args.min:
        print(f"Need at least {args.min} samples. Keep paper trading!")
        sys.exit(1)

    df = store.get_training_data()
    metrics = model.train(df)
    print(f"Training complete: {metrics}")
```

---

### TASK 16 — Integrate model into the trading loop in `run.py`

Wire the `SwingbotModel` into the main `job()` function.

**Initialize at startup:**
```python
from ml.model import SwingbotModel
ml_model = SwingbotModel()
```

**During entry evaluation**, after scanner score passes:
```python
# Extract ML features for this candidate
ml_features = FeatureEngine.extract_ml_features(
    df=df,
    scanner_score=candidate['score'],
    breakout_detected=candidate.get('breakout_detected', False),
    macro_scale=status.get('risk_scale', 1.0),
    fear_greed=sentiment_engine.get_score() or 50.0
)

# Model gate — only enter if model agrees
enter, confidence, ml_reason = ml_model.should_enter(
    ml_features, candidate['score']
)

if not enter:
    logger.warning(f"[ML_SKIP] {sym}: {ml_reason}")
    continue

# Apply confidence-based sizing boost
if confidence >= 0.85:
    size *= 1.5   # High conviction — size up (from article)
    logger.warning(f"[ML_BOOST] {sym}: {confidence:.0%} confidence → 1.5x size")

status['ai_confidence'] = confidence
```

**Save features when trade opens:**
```python
if order:
    ml_features['trade_id'] = order.id
    store.save_trade_features(ml_features)
```

**Update outcome when trade closes:**
```python
# After broker.place_order(exit_sig, pos.amount)
store.update_trade_outcome(
    trade_id=pos.id,
    outcome=1 if pnl > 0 else 0,
    pnl=pnl,
    pnl_pct=pnl_pct,
    exit_reason=exit_sig.reason.value
)
```

---

### TASK 17 — Sharpe Ratio + MAE/MFE in `reports/daily_report.py`

The article is clear: win rate is not the right metric.
Add proper performance measurement.

**Add to `daily_report.py`:**

```python
import numpy as np

def calculate_sharpe_ratio(self, date_str: str, risk_free_rate: float = 0.0) -> float:
    """
    Sharpe Ratio = (Mean log return - Risk free rate) / Std of log returns
    Uses log returns as per the article (correct for large moves).

    SR < 1.0  = poor
    SR 1-2    = good
    SR > 2.0  = excellent
    """
    trades = self.store.get_closed_trades_for_date(date_str)
    if len(trades) < 3:
        return 0.0

    # Log returns (article technique — correct for compounding)
    log_returns = []
    for t in trades:
        if t.get('pnl_percent') and t['pnl_percent'] != 0:
            import math
            p0 = 100.0
            p1 = 100.0 * (1 + t['pnl_percent'] / 100)
            log_returns.append(math.log(p1 / p0))

    if len(log_returns) < 3:
        return 0.0

    mean_r = np.mean(log_returns)
    std_r  = np.std(log_returns)
    if std_r == 0:
        return 0.0

    return float((mean_r - risk_free_rate) / std_r)

def calculate_mae_mfe(self, trade_id: str) -> dict:
    """
    MAE = Maximum Adverse Excursion (deepest red before close)
    MFE = Maximum Favorable Excursion (highest green before close)
    Reveals: are stops too tight? Are we exiting winners too early?
    """
    # Requires intra-trade candle data — implement if available
    # Returns dict with 'mae_pct' and 'mfe_pct'
    pass
```

**Add Sharpe Ratio to the daily report JSON output.**
**Add Sharpe Ratio to the dashboard History tab** (`/api/history` endpoint).
**Log Sharpe Ratio in the daily summary** printed to console/logs.

---

## REQUIREMENTS UPDATE

Add these to `requirements.txt`:
```
scikit-learn>=1.3.0
numpy>=1.24.0
flask>=3.0.0
```

---

## CONSTRAINTS — NON-NEGOTIABLE

- Never break paper trading mode — all new code must work in paper mode
- Never remove or bypass circuit breakers or safety systems
- Never use Polymarket data for trading signals (haram) — only use the
  Random Forest technique applied to crypto OHLCV + exchange data
- Never widen a stop loss — only narrow or trail it
- Never risk more than 5% of balance on a single trade
- All new files must have type hints and docstrings
- Bot must still work with `--once` flag for single-cycle testing
- Dashboard must work even if ML model is not yet trained (graceful fallback)
- Model training must be triggered manually (`python -m ml.trainer`)
  not automatically — the human decides when to train

---

## TESTING SEQUENCE

Run these in order after implementation:

```bash
# 1. Verify all imports work
python -c "from ml.model import SwingbotModel; print('ML OK')"
python -c "from execution.broker_bybit import BybitBroker; print('Bybit OK')"
python -c "from dashboard.routes import create_app; print('Dashboard OK')"

# 2. Verify storage
python -m storage.check_store

# 3. Single cycle paper test
python run.py --once --lang en

# 4. Fast cycle test (2 min interval, 1 cycle)
python run.py --once --fast --lang en

# 5. Check ML trainer (should say "need more samples")
python -m ml.trainer

# 6. Check dashboard starts
python run.py --once --lang en
# Open http://localhost:8080 in browser
# Should show login page
```

---

## THE ROADMAP AFTER IMPLEMENTATION

```
Week 1-2  → Run in PAPER mode, 10-min scans, collect trade data
Week 3-4  → Reach 50+ paper trades, train first Random Forest model
Week 5    → Evaluate model Sharpe Ratio — if SR > 1.5, consider live
Week 6    → Create Bybit account, deposit $100, enable LIVE mode
Week 6-20 → Compound $100 → $1000 with AI-powered signals
```

---

## FINAL REMINDER

Read `INVESTOR_MINDSET.md` before every implementation decision.

The 10 Laws are not suggestions. The checklist is not optional.
The circuit breakers are sacred. The model is a tool — not a god.

When the model says 85% confidence but the macro is collapsing,
the macro veto wins. Always.

This bot is built to be the trader you cannot be:
**emotionless, tireless, mathematically sound, and disciplined.**
