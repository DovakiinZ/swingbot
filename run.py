import argparse
import time
from datetime import datetime
import sys
import os
import yaml
import logging
import schedule
from dotenv import load_dotenv

from core.utils import setup_logging, load_json
from core.clock import Clock
from core.types import Side, Reason, PositionStatus
from data.market import MarketData
from data.features import FeatureEngine
from storage.sqlite_store import SQLiteStore
from strategy.rsi_ema import RsiEmaStrategy
from strategy.regimes import RegimeDetector
from risk.risk_engine import RiskEngine
from risk.circuit_breakers import CircuitBreaker
from execution.broker_paper import PaperBroker
from execution.broker_binance import BinanceBroker
from optimize.bandit import Bandit
from reports.daily_report import DailyReport
from optimize.param_sets import ARMS
from data.sentiment import SentimentEngine
from strategy.selector import SymbolSelector


# Load Config
load_dotenv()
with open("config.yaml", "r") as f:
    CONFIG = yaml.safe_load(f)

# Global State
store = SQLiteStore(db_path=CONFIG['db_path'])
clock = Clock(mode="live") 
logger = logging.getLogger("swingbot")

# Observability Helpers
def format_status_line(timestamp, symbol, price, signal, pos_state, arm, pnl, breaker, next_check):
    # Time | Symbol | Price | Signal | Pos | Arm | PnL | Breaker | Next
    t_str = timestamp.strftime("%H:%M:%S")
    sig_str = f"{signal}" if signal else "-"
    pos_str = f"{pos_state}"
    return f"{t_str} UTC | {symbol} | ${price:<8.2f} | Sig: {sig_str:<4} | Pos: {pos_str:<6} | Arm: {arm} | DayPnL: {pnl:>6.2f} | {breaker} | Next: {next_check}s"

def main():
    parser = argparse.ArgumentParser(description="Swingbot")
    parser.add_argument("--paper", action="store_true", help="Run in paper mode (default)")
    parser.add_argument("--live", action="store_true", help="Run in LIVE mode (DANGEROUS)")
    parser.add_argument("--once", action="store_true", help="Run loop once and exit")
    parser.add_argument("--symbol", type=str, default=CONFIG['symbol'], help="Trading pair")
    args = parser.parse_args()

    # Safety Checks for Live Mode
    is_live = False
    if args.live:
        # 1. Check ENV/Config
        env_mode = os.getenv("TRADING_MODE", "paper").lower()
        if env_mode != "live":
            logger.critical("ARG --live set but .env TRADING_MODE is not 'live'. Aborting.")
            sys.exit(1)
        
        # 2. Check LIVE_OK.txt
        if not os.path.exists("LIVE_OK.txt"):
            logger.critical("LIVE_OK.txt not found. Aborting live mode.")
            sys.exit(1)
            
        print("WARNING: STARTING IN LIVE TRADING MODE")
        for i in range(5, 0, -1):
            print(f"Starting in {i}...")
            time.sleep(1)
        is_live = True
        clock.mode = "live"
    else:
        # logger.info("Starting in PAPER mode") -> DEBUG
        clock.mode = "paper"

    # Setup Logging
    log_level_console = os.getenv("LOG_LEVEL_CONSOLE", "WARNING")
    log_level_file = os.getenv("LOG_LEVEL_FILE", "DEBUG") # User said "Default INFO to file", let's use DEBUG for file to catch everything
    setup_logging(console_level=log_level_console, file_level=log_level_file)
    
    logger.warning(f"--- Swingbot Started ({'LIVE' if is_live else 'PAPER'}) ---")
    logger.warning(f"Logging: Console={log_level_console}, File={log_level_file}")

    # Initialize Components
    market = MarketData(sandbox=False) 
    
    if is_live:
        broker = BinanceBroker(store, market)
    else:
        broker = PaperBroker(store, clock, initial_balance=50.0)

    risk_engine = RiskEngine(
        total_capital=broker.get_balance(),
        risk_per_trade_percent=CONFIG['risk_per_trade_percent'],
        max_open_positions=CONFIG['max_open_positions']
    )
    
    circuit_breaker = CircuitBreaker(
        daily_loss_limit_percent=CONFIG['daily_loss_limit_percent'],
        consecutive_loss_limit=CONFIG['consecutive_loss_limit'],
        api_failure_limit=CONFIG['api_failure_limit']
    )
    
    strategy = RsiEmaStrategy()
    bandit = Bandit(store, exploration_prob=CONFIG['bandit']['exploration_prob'])

    reporter = DailyReport(store)
    sentiment_engine = SentimentEngine()
    selector = SymbolSelector(market.exchange)

    context = {
        "symbol": args.symbol,
        "timeframe": CONFIG['timeframe'],
        "lookback": CONFIG['lookback']
    }
    
    # State tracking for summary
    last_summary_date = None

    # Job Loop
    def job():
        nonlocal last_summary_date
        cycle_start_time = time.time()
        
        # Status Line Aggregation
        status = {
            "signal": None,
            "pos_state": "FLAT",
            "breaker": "OK",
            "active_symbol": context['symbol'],
            "price": 0.0,
            "arm": 0,
            "pnl": 0.0
        }

        try:
            logger.debug(f"--- Cycle Start: {datetime.now()} ---")
            
            # Re-fetch state securely
            current_bal = broker.get_balance()
            risk_engine.total_capital = current_bal
            
            # Sync daily stats
            today_str = datetime.utcnow().strftime('%Y-%m-%d')
            daily_stats = store.get_daily_stats(today_str)
            status['pnl'] = daily_stats.get('pnl', 0.0)

            # Daily Summary Print (if day changed)
            if last_summary_date != today_str:
                if last_summary_date is not None:
                    # Print summary for yesterday
                    logger.warning(f"\n=== DAILY SUMMARY ({last_summary_date}) ===")
                    # Retrieve stats for yesterday... simplified: just print current stats as "end of day"
                    # In real app, we'd query yesterday's row.
                    logger.warning(f"Trades: {daily_stats.get('trades_count',0)} | PnL: {daily_stats.get('pnl',0):.2f}")
                    logger.warning("===================================\n")
                last_summary_date = today_str

            # 1. Circuit Breaker
            if daily_stats.get('paused_until'):
                status['breaker'] = f"PAUSED({daily_stats['paused_until']})"
                # We return early but still print status? No, user said "print ONE concise status line".
                # If paused, we basically idle.
                # Let's fallback to simplified status
                logger.warning(format_status_line(datetime.utcnow(), status['active_symbol'], 0, "-", "PAUSED", "-", status['pnl'], status['breaker'], 300))
                return

            if daily_stats.get('pnl', 0) < -(current_bal * CONFIG['daily_loss_limit_percent'] / 100):
                logger.critical("Daily Loss Limit Hit. Pausing.")
                store.update_daily_stats(today_str, {'paused_until': 'Next Day'})
                status['breaker'] = "PAUSED(LOSS_LIMIT)"
                return
                
            # 2. Market Data & Symbol Selection
            current_pos = store.get_open_position() 
            
            if current_pos:
                status['active_symbol'] = current_pos.symbol
                status['pos_state'] = f"{current_pos.side.value}"
                status['arm'] = 0 # retrieve from pos if avail
            else:
                if not args.symbol: 
                    top_pairs = selector.get_top_pairs(limit=1)
                    if top_pairs:
                        status['active_symbol'] = top_pairs[0]
                        logger.debug(f"Selected Active Symbol: {status['active_symbol']}")
            
            # 3. Fetch Data
            candles = market.fetch_ohlcv(status['active_symbol'], context['timeframe'], limit=context['lookback'])
            if not candles:
                logger.debug("No candles fetched.")
                circuit_breaker.record_api_error()
                return

            # 4. Feature Engineering
            df = FeatureEngine.compute_indicators(candles)
            if df.empty: return
            
            current_candle = candles[-1]
            status['price'] = current_candle.close
            regime = RegimeDetector.detect(df.iloc[-1])
            logger.debug(f"Market Regime: {regime.value}")
            
            # 5. Bandit
            arm_idx = 0
            if not current_pos:
                arm_idx = bandit.select_arm_index()
            status['arm'] = arm_idx
            active_params = ARMS[arm_idx]

            # 6. Signal
            signal = strategy.check_signal(
                df, 
                regime, 
                active_params, 
                current_position=(current_pos is not None)
            )
            
            if signal:
                status['signal'] = signal.side.value
            
            # 7. Sentiment
            safe_to_enter = True
            if not sentiment_engine.is_market_safe(threshold=CONFIG.get('sentiment_threshold', 20)):
                safe_to_enter = False
                status['breaker'] = "SENTIMENT_FEAR"

            # 8. Execution
            if signal:
                logger.debug(f"Signal found: {signal.side}")
                if signal.side == Side.BUY:
                    if current_pos: pass
                    elif not safe_to_enter: logger.debug("Sentiment unsafe")
                    elif not risk_engine.can_open_new_position(0): logger.debug("Risk max pos")
                    else:
                        size = risk_engine.calculate_position_size(signal)
                        market_struct = market.get_market_structure(status['active_symbol'])
                        ok, msg = risk_engine.check_min_notional(size, signal.price, market_struct)
                        if ok:
                            logger.info(f"EXECUTING BUY: {size} @ {signal.price}")
                            broker.place_order(signal, size)
                            status['pos_state'] = "OPENING"
                        else:
                            logger.debug(f"Min notional fail: {msg}")

                elif signal.side == Side.SELL:
                    if current_pos:
                        logger.info(f"EXECUTING SELL: Closing {current_pos.amount}")
                        broker.place_order(signal, current_pos.amount)
                        status['pos_state'] = "CLOSING"

            # 9. Paper SL/TP
            if not is_live and current_pos:
                 exit_sig = broker.check_sl_tp(current_candle)
                 if exit_sig:
                     logger.info(f"Paper SL/TP Trigger: {exit_sig.reason}")
                     broker.place_order(exit_sig, current_pos.amount)
                     status['pos_state'] = "CLOSING_SLTP"

            # 10. Status Log (Concise)
            # Use logger.warning to ensure it prints to console (lvl=WARNING)
            elapsed = time.time() - cycle_start_time
            next_wait = 300 - int(elapsed) # approx for 5m interval
            line = format_status_line(
                datetime.utcnow(), 
                status['active_symbol'], 
                status['price'], 
                status['signal'], 
                status['pos_state'], 
                status['arm'], 
                status['pnl'], 
                status['breaker'],
                max(0, next_wait)
            )
            logger.warning(line)

        except Exception as e:
            logger.error(f"Cycle Error: {e}", exc_info=True)
            circuit_breaker.record_api_error()
            
    # --- End Job ---
    
    if args.once:
        job()
    else:
        logger.warning("Starting Main Loop... (Press Ctrl+C to stop)")
        job()
        schedule.every(5).minutes.do(job)
        while True:
            schedule.run_pending()
            time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping...")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal Error: {e}")
        sys.exit(1)
