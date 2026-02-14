# Swingbot

A safe, self-improving crypto swing trading bot for Binance Spot.

## Features
- **Safety First**: Paper trading default, strict risk limits, hard kill-switches.
- **Deterministic Strategy**: RSI + EMA trend following with regime detection.
- **Self-Optimizing**: Uses a Multi-Armed Bandit (Thompson Sampling) to select parameters.
- **Architecture**: Clean separation of concerns (Core, Data, Strategy, Risk, Execution).

## Setup

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configuration**
   - Copy `.env.example` to `.env`
   - Edit `config.yaml` to adjust risk settings (defaults are safe for 50 USDT).

3. **Run in Paper Mode (Default)**
   ```bash
   python run.py
   ```
   This will use simulated money and print trades to console/logs.

## Live Trading (CAUTION)

1. **Prerequisites**:
   - Valid Binance API Keys (Trade + Read ONLY, NO Withdrawals).
   - Add keys to `.env`.

2. **Enable Live Mode**:
   - Set `TRADING_MODE=live` in `.env`.
   - Create a file named `LIVE_OK.txt` in the root directory.
     ```bash
     echo "CONFIRMED" > LIVE_OK.txt
     ```

3. **Run**:
   ```bash
   python run.py --live
## Key Files
- `run.py`: Entry point.
- `config.yaml`: Strategy and risk params.
- `strategy/rsi_ema.py`: Trading logic.
- `risk/`: Position sizing and circuit breakers.
- `reports/`: Generates daily JSON reports.

## 🚨 Safety First
This bot is designed with **Capital Preservation** as the #1 priority.

### Hard Kill-Switches (Circuit Breakers)
The bot will **STOP TRADING** for the rest of the UTC day if:
1.  **Daily Loss Limit**: Equity drops by > 2% (default).
2.  **Consecutive Losses**: 3 losing trades in a row.
3.  **API Failures**: 2 critical API errors occur.

### Live Trading Requirements (Strict)
To enable real trading, you must pass 3 checks:
1.  **Config**: `live: true` in `config.yaml`.
2.  **Env**: `TRADING_MODE=live` in `.env`.
3.  **Gatefile**: `LIVE_OK.txt` exists in root.

If *any* of these are missing, the bot forces **PAPER** mode and prints a warning.

## ❓ Help / مساعدة
To see a full list of commands in Arabic and English:
```bash
python run.py --guide
```

## 🌍 Localization (Arabic Support)
To run in Arabic mode (default):
```bash
python run.py --lang ar
```
Or set `lang: "ar"` in `config.yaml`.

## ⚡ Quick Start

### 1. Install
```bash
pip install -r requirements.txt
```

### 2. Verify Install
```bash
python -m storage.check_store
```

### 3. Run (Paper Mode)
```bash
python run.py
```
Outputs one status line per cycle. Detailed logs in `logs/`.

### 4. Configuration
Edit `config.yaml` to adjust risk params:
- `risk_per_trade_percent`: Position sizing (default 1.0%).
- `max_open_positions`: Hard limit (default 1).
- `sentiment_threshold`: Min Fear & Greed score to enter (default 20).

## 🧠 Optimization (Bandit)
The bot uses a multi-armed bandit algorithm to learn.
- It tracks the **R-Multiple** (Risk:Reward ratio) of every trade.
- It dynamically adjusts the probability of selecting different strategy parameters ("Arms").
- Arms are defined in `optimize/param_sets.py`.

## License
MIT
