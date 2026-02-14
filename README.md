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
   ```

## Key Files
- `run.py`: Entry point.
- `config.yaml`: Strategy and risk params.
- `strategy/rsi_ema.py`: Trading logic.
- `risk/`: Position sizing and circuit breakers.
- `reports/`: Generates daily JSON reports.

## Safety Mechanisms
- **Daily Loss Limit**: Stops if drawdown > 2% in a day.
- **Consecutive Losses**: Stops after 3 losses in a row.
- **API Failures**: Stops after 2 contiguous API errors.
- **Walk-Forward Validation**: Validates strategy arms before switching.

## License
MIT
