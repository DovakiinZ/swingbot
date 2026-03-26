# SWINGBOT — FEATURES PROMPT
# Goal Tracker + Committee History + Live MEXC Prices

> Add these 3 features to the existing dashboard and codebase.
> All data updates in real-time without page refresh.

---

## FEATURE 1 — LIVE MEXC PRICES IN DASHBOARD

### How it works

Connect directly to MEXC WebSocket API to stream live prices
for the top scanned symbols. No polling — true real-time push.

### In `dashboard/routes.py` add:

```python
GET /api/prices
```

Returns live prices fetched via ccxt.mexc for all symbols
currently in the scan universe:

```json
{
  "BTC/USDT": {
    "price":      67420.50,
    "change_24h": +2.31,
    "high_24h":   68100.00,
    "low_24h":    65800.00,
    "volume_24h": 1284000000,
    "bid":        67418.00,
    "ask":        67422.00,
    "updated_at": "2024-03-15T10:22:31Z"
  },
  "ETH/USDT": { ... },
  "SOL/USDT": { ... }
}
```

Fetched using:
```python
import ccxt

exchange = ccxt.mexc({'enableRateLimit': True})

def get_live_prices(symbols: list[str]) -> dict:
    """
    Fetch live tickers for all symbols from MEXC.
    Uses fetch_tickers() for batch efficiency.
    Falls back to individual fetch_ticker() if batch fails.
    Caches result for 5 seconds to avoid rate limits.
    """
    tickers = exchange.fetch_tickers(symbols)
    return {
        symbol: {
            'price':      t['last'],
            'change_24h': t['percentage'],
            'high_24h':   t['high'],
            'low_24h':    t['low'],
            'volume_24h': t['quoteVolume'],
            'bid':        t['bid'],
            'ask':        t['ask'],
            'updated_at': datetime.utcnow().isoformat() + 'Z'
        }
        for symbol, t in tickers.items()
        if t and t.get('last')
    }
```

### In the Dashboard — Scanner List (Status Tab)

Replace the static mock coin list with live data.
Each coin row updates every **5 seconds** via:

```javascript
async function fetchLivePrices() {
    const res = await fetch('/api/prices');
    const prices = await res.json();
    updateScannerRows(prices);
}
setInterval(fetchLivePrices, 5000);
```

Each coin row shows:
```
┌─────────────────────────────────────────┐
│ ₿  BTC/USDT                            │
│    $67,420.50          +2.31% ▲        │  ← green if +, red if -
│    H: $68,100  L: $65,800              │  ← 24h high/low
│    Vol: $1.28B                          │
│                    Score: 87  BUY 🔥   │
└─────────────────────────────────────────┘
```

Show a live green dot "● LIVE" next to the section title
to indicate prices are streaming from MEXC.

If prices fail to load (API error / no connection):
- Show last known price with "⚠ Delayed" badge
- Never show empty cells

### In Open Positions Tab

For each open position, fetch the current MEXC price
and calculate unrealized P&L in real-time:

```javascript
// Update P&L every 5 seconds using live prices
function updatePositionPnL(position, currentPrice) {
    const pnl = (currentPrice - position.entry_price) * position.amount;
    const pnlPct = ((currentPrice - position.entry_price) / position.entry_price) * 100;
    // Update card color and values
}
```

---

## FEATURE 2 — GOAL TRACKER ($100 → $1000)

### Concept

A visual progress bar always visible on the dashboard
showing exactly how far the bot has come toward the goal.
Updates every cycle with the real balance.

### In `/api/status` add these fields:

```json
"goal_tracker": {
  "start_balance":    100.00,
  "current_balance":  247.83,
  "target_balance":   1000.00,
  "progress_pct":     16.42,
  "phase":            1,
  "phase_name":       "Phase 1",
  "phase_start":      100.00,
  "phase_target":     250.00,
  "phase_progress_pct": 98.55,
  "trades_completed": 23,
  "trades_target":    100,
  "estimated_trades_remaining": 77,
  "on_track":         true,
  "projected_completion": "~67 more trades at current win rate"
}
```

### Calculation logic in `core/goal_tracker.py`:

```python
class GoalTracker:
    """
    Tracks progress from starting balance to $1000 target.
    Calculates phase, overall progress, and projected completion.
    """

    PHASES = [
        {"phase": 1, "start": 0,    "target": 250,  "risk_pct": 3.0},
        {"phase": 2, "start": 250,  "target": 500,  "risk_pct": 3.5},
        {"phase": 3, "start": 500,  "target": 1000, "risk_pct": 4.0},
    ]

    def __init__(self, config: dict, store):
        self.start_balance  = config.get('base_balance', 100.0)
        self.target_balance = config.get('goal_balance', 1000.0)
        self.store = store

    def get_status(self, current_balance: float) -> dict:
        """
        Returns full goal tracker status for dashboard.

        Overall progress:
          progress_pct = (current - start) / (target - start) * 100

        Current phase:
          Which PHASE bracket does current_balance fall in?

        Phase progress:
          How far through THIS phase are we?

        Projection:
          Based on avg P&L per trade, how many more trades needed?
        """
        overall_progress = min(
            (current_balance - self.start_balance) /
            (self.target_balance - self.start_balance) * 100,
            100.0
        )

        # Current phase
        current_phase = self.PHASES[0]
        for phase in self.PHASES:
            if current_balance >= phase['start']:
                current_phase = phase

        phase_progress = min(
            (current_balance - current_phase['start']) /
            (current_phase['target'] - current_phase['start']) * 100,
            100.0
        )

        # Projection
        stats = self.store.get_overall_stats()
        avg_pnl_per_trade = stats.get('avg_pnl', 0)
        if avg_pnl_per_trade > 0:
            remaining = self.target_balance - current_balance
            est_trades = int(remaining / avg_pnl_per_trade)
            projection = f"~{est_trades} more trades at current pace"
        else:
            projection = "Collecting data..."

        return {
            'start_balance':          self.start_balance,
            'current_balance':        current_balance,
            'target_balance':         self.target_balance,
            'progress_pct':           round(overall_progress, 2),
            'phase':                  current_phase['phase'],
            'phase_name':             f"Phase {current_phase['phase']}",
            'phase_start':            current_phase['start'],
            'phase_target':           current_phase['target'],
            'phase_progress_pct':     round(phase_progress, 2),
            'trades_completed':       stats.get('total_trades', 0),
            'trades_target':          100,
            'on_track':               avg_pnl_per_trade > 0,
            'projected_completion':   projection,
        }
```

### Dashboard UI — Status Tab (add below balance hero card)

```
┌──────────────────────────────────────┐
│  🎯 Goal: $100 → $1,000             │
│                                      │
│  $247.83 ━━━━━━━━━░░░░░░░░ $1,000   │
│  ████████████████░░░░░░░░░░░░░░░░   │
│  16.4% complete                      │
│                                      │
│  ── Phase 1: $100 → $250 ──         │
│  ████████████████████████████████░░ │
│  $247.83 / $250  (98.6% done) 🔥    │
│                                      │
│  23 trades · ~77 more to target     │
└──────────────────────────────────────┘
```

Design details:
- Overall progress bar: gradient from `#3d8bff` → `#00e5a0`
- Phase progress bar: solid `#00e5a0`
- When phase completes → show celebration "🎉 Phase 1 Complete!"
- When near phase target (>90%) → pulse animation on bar
- "$1000" target glows gold when balance > $900

---

## FEATURE 3 — COMMITTEE DECISION LOG

### Concept

A dedicated tab showing every committee decision ever made.
Shows which agents voted what, who used veto, and what happened
to the trade. Over time, reveals which advisor is most accurate.

### New endpoint in `dashboard/routes.py`:

```python
GET /api/committee/history?limit=50&symbol=BTC/USDT
```

Returns:
```json
{
  "decisions": [
    {
      "id":             "abc123",
      "timestamp":      "2024-03-15T10:22:00Z",
      "symbol":         "BTC/USDT",
      "approved":       true,
      "final_score":    78.5,
      "size_multiplier": 1.0,
      "veto_by":        null,
      "verdicts": {
        "Technical Analyst": {"score": 87, "rec": "BUY"},
        "Financial Advisor":  {"score": 72, "rec": "BUY"},
        "Macro Advisor":      {"score": 65, "rec": "BUY"},
        "AI Advisor":         {"score": 84, "rec": "BUY"},
        "Risk Manager":       {"score": 80, "rec": "BUY"}
      },
      "trade_executed": true,
      "trade_outcome":  "WIN",
      "trade_pnl":      8.42
    },
    ...
  ],
  "agent_accuracy": {
    "AI Advisor":         {"decisions": 45, "correct": 38, "accuracy": 84.4},
    "Technical Analyst":  {"decisions": 45, "correct": 32, "accuracy": 71.1},
    "Risk Manager":       {"decisions": 45, "correct": 31, "accuracy": 68.9},
    "Financial Advisor":  {"decisions": 45, "correct": 30, "accuracy": 66.7},
    "Macro Advisor":      {"decisions": 45, "correct": 28, "accuracy": 62.2}
  },
  "veto_stats": {
    "total_vetoes":  8,
    "macro_vetoes":  5,
    "risk_vetoes":   3,
    "veto_saved_losses": 3
  }
}
```

### In `storage/sqlite_store.py` add:

```python
def get_committee_history(
    self,
    limit: int = 50,
    symbol: str = None
) -> list[dict]:
    """
    Returns committee decisions with trade outcomes joined in.
    Calculates per-agent accuracy by comparing recommendation
    to actual trade result (WIN/LOSS).
    """

def get_agent_accuracy(self) -> dict:
    """
    For each agent, count how many times their recommendation
    matched the actual trade outcome.
    "Correct" = agent said BUY and trade was WIN
              = agent said SELL/HOLD and trade was skipped or LOSS
    """

def get_veto_stats(self) -> dict:
    """
    How many vetoes happened, by whom, and how many
    of those vetoes were "correct" (trade would have lost).
    """
```

### Dashboard UI — New "Committee" Tab (6th tab)

Add a 6th tab to the tab bar with a gavel icon ⚖️

**Section 1 — Agent Accuracy Leaderboard:**
```
┌──────────────────────────────────────┐
│  🏆 Agent Accuracy (45 decisions)   │
│                                      │
│  🥇 AI Advisor          84.4%  ████ │
│  🥈 Technical Analyst   71.1%  ███░ │
│  🥉 Risk Manager        68.9%  ███░ │
│     Financial Advisor   66.7%  ███░ │
│     Macro Advisor       62.2%  ██░░ │
│                                      │
│  Vetoes: 8 total · 3 saved losses   │
└──────────────────────────────────────┘
```

**Section 2 — Filter bar:**
```
[ All Symbols ▾ ]  [ All Outcomes ▾ ]  [ Last 30 days ▾ ]
```

**Section 3 — Decision History list:**

Each decision is a collapsible card:

Approved trade that won:
```
┌──────────────────────────────────────┐  ← green left border
│ ✅ BTC/USDT  78.5/100  +$8.42 WIN   │
│ 15 Mar 2024 10:22                    │
│                              ▼       │  ← tap to expand
└──────────────────────────────────────┘

Expanded:
│  📊 Technical   87  ✅ BUY           │
│  💼 Financial   72  ✅ BUY           │
│  🌍 Macro       65  ✅ BUY           │
│  🤖 AI          84  ✅ BUY           │
│  ⚡ Risk        80  ✅ BUY           │
│  Trade result: +$8.42 (+3.4%) TP ✅  │
```

Vetoed trade:
```
┌──────────────────────────────────────┐  ← red left border
│ 🚫 ETH/USDT  VETOED  Macro Advisor  │
│ 14 Mar 2024 16:45                    │
└──────────────────────────────────────┘

Expanded:
│  Veto reason: Extreme fear (F&G=12) │
│  Scores: Tech 82 | Fin 71 | AI 76   │
│  Would have been: (unknown)          │
```

Rejected (low score) trade:
```
┌──────────────────────────────────────┐  ← gray left border
│ ❌ SOL/USDT  54.2/100  REJECTED     │
│ 14 Mar 2024 08:11                    │
└──────────────────────────────────────┘
```

---

## FILES TO CREATE / MODIFY

```
NEW:
  core/goal_tracker.py              ← Goal tracking logic

MODIFY:
  dashboard/routes.py               ← /api/prices, /api/committee/history
  dashboard/templates/index.html    ← Live prices + Goal Tracker + Committee tab
  storage/sqlite_store.py           ← get_committee_history(), get_agent_accuracy()
  run.py                            ← Initialize GoalTracker, add to status
```

---

## CONFIG UPDATES

Add to `config.yaml`:
```yaml
# Goal Tracker
goal_balance: 1000.0        # Target balance ($)
base_balance: 100.0         # Starting balance ($)

# Live Prices
live_prices:
  enabled: true
  refresh_seconds: 5        # How often dashboard fetches prices
  symbols_to_track: []      # Empty = auto from scan universe
```

---

## RUN.PY CHANGES

```python
from core.goal_tracker import GoalTracker

# Initialize
goal_tracker = GoalTracker(CONFIG, store)

# In job() — add to dashboard_state
dashboard_state['goal_tracker'] = goal_tracker.get_status(current_bal)

# In /api/status response
"goal_tracker": dashboard_state.get('goal_tracker', {})
```

---

## CONSTRAINTS

```
✅ Live prices fall back to last known price if MEXC unreachable
✅ Goal tracker works even with 0 trades (shows 0% progress)
✅ Committee history shows "outcome unknown" for vetoed trades
✅ Agent accuracy only calculated after 10+ decisions minimum
✅ All price displays show 2 decimal places for USDT pairs
✅ 24h change shows + or - sign with color (green/red)
✅ Tab bar handles 6 tabs on small screens (smaller icons/text)
✅ Live price dot animates only when prices are actually updating
```

---

## TESTING

```bash
# 1. Test live prices endpoint
python run.py --once --lang en
curl http://localhost:8080/api/prices
# Should return prices for top scanned symbols

# 2. Test goal tracker
curl http://localhost:8080/api/status | python -m json.tool | grep goal_tracker

# 3. Test committee history
curl http://localhost:8080/api/committee/history
# Will be empty on first run — that's expected

# 4. Open dashboard
# http://localhost:8080
# Check:
#   ✅ Status tab → Live prices with ● LIVE dot
#   ✅ Status tab → Goal Tracker progress bars
#   ✅ New ⚖️ Committee tab visible
#   ✅ Prices update every 5 seconds without page refresh
```
