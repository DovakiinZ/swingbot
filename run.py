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
clock = Clock(mode="live") # Changed later if necessary
logger = logging.getLogger("swingbot")

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
        logger.info("Starting in PAPER mode")
        clock.mode = "paper"
        # For paper, we still sync with real time in this run loop, 
        # unless we are backtesting which is a different script.
        # So clock.now_ms() essentially calls time.time() but ensures we know it's paper.

    setup_logging()

    # Initialize Components
    market = MarketData(sandbox=False) 
    # Note: Using public data for paper trading is fine.
    
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
    sentiment_engine = SentimentEngine()
    selector = SymbolSelector(market.exchange)

    context = {
        "symbol": args.symbol,
        "timeframe": CONFIG['timeframe'],
        "lookback": CONFIG['lookback']
    }

    def job():
        logger.info(f"--- Cycle Start: {datetime.now()} ---")
        
        # 1. Circuit Breaker Check
        if circuit_breaker.is_tripped:
            logger.warning(f"Circuit Breaker TRIPPED: {circuit_breaker.trip_reason}. Skipping cycle.")
            return

        # 1.5 Sentiment Check
        if not sentiment_engine.is_market_safe(threshold=CONFIG.get('sentiment_threshold', 20)):
            logger.warning("Market Sentiment Unsafe. Updates only, no new entries.")
            # We still proceed to process logic for exits, but flag it
            safe_to_enter = False
        else:
            safe_to_enter = True

        # 2. Update Capital
        current_bal = broker.get_balance()
        risk_engine.total_capital = current_bal
        
        # 3. Market Data
        # 3. Market Data & Symbol Selection
        # If no position, dynamic select. If position, stick to it.
        active_symbol = context['symbol']
        current_pos = broker.get_open_position()
        
        if not current_pos:
            # Dynamic selection every cycle? Or cache it?
            # Let's simple check top 1 volume pair if context['symbol'] not locked
            # But user wants global suggestion.
            # Simplified: Update active_symbol to best pair if we are flat.
            top_pairs = selector.get_top_pairs(limit=1)
            if top_pairs:
                active_symbol = top_pairs[0]
                logger.info(f"Selected Active Symbol: {active_symbol}")
        else:
            active_symbol = current_pos.symbol

        try:
            candles = market.fetch_ohlcv(active_symbol, context['timeframe'], limit=context['lookback'])
            if not candles:
                logger.warning("No candles fetched.")
                circuit_breaker.record_api_error()
                return
        except Exception as e:
            logger.error(f"Market data fetch error: {e}")
            circuit_breaker.record_api_error()
            return

        # 4. Feature Engineering
        # Use default params for regime detection
        df = FeatureEngine.compute_indicators(candles)
        current_candle = candles[-1]
        
        if df.empty:
            return

        # 5. Regime Detection
        regime = RegimeDetector.detect(df.iloc[-1])
        logger.info(f"Market Regime: {regime.value} | Price: {current_candle.close}")

        # 6. Optimization: Select Arm
        # (Only if no open position? Or can we switch strat mid-flight? usually NO)
        current_pos = broker.get_open_position()
        
        arm_idx = 0
        if not current_pos:
            arm_idx = bandit.select_arm_index()
            # Also run walk-forward validation if switching arms, skipped for MVP simplicity
        else:
            # Stick to the arm that opened the position if possible, 
            # OR retrieve param set from position object
            pass 
            
        active_params = ARMS[arm_idx]
        if current_pos and current_pos.strategy_params:
            active_params = current_pos.strategy_params

        # Re-compute features if needed for specific arm (if different from default)
        # For MVP we used standard features in strategy check mostly, but let's assume standard is enough 
        # unless params require different window lengths.
        # df_arm = FeatureEngine.compute_dynamic_features(candles, active_params.to_dict()) 
        # Using standard df for now.

        # 7. Strategy Signal
        signal = strategy.check_signal(
            df, 
            regime, 
            active_params, 
            current_position=(current_pos is not None)
        )
        
        if signal:
            logger.info(f"Signal Generated: {signal.side} | Reason: {signal.reason}")
            
            # 8. Risk Check Implementation
            if signal.side == Side.BUY:
                if not safe_to_enter:
                    logger.info("Signal Ignored: Market Sentiment Unsafe.")
                    return
                # Check Entry Limits
                # Check Max positions
                if not risk_engine.can_open_new_position(1 if current_pos else 0):
                    logger.info("Risk: Max positions reached. Ignored.")
                    return

                # Calculate Size
                size = risk_engine.calculate_position_size(signal)
                
                # Check Min Notional
                market_struct = market.get_market_structure(context['symbol'])
                ok, msg = risk_engine.check_min_notional(size, signal.price, market_struct)
                if not ok:
                    logger.info(f"Risk: Min notional failed. {msg}")
                    return
                    
                # Execute
                logger.info(f"Placing BUY Order: Size {size:.5f} @ {signal.price}")
                broker.place_order(signal, size)
                
            elif signal.side == Side.SELL:
                # Exit
                if current_pos:
                    logger.info(f"Placing SELL Order to Close: {current_pos.amount:.5f}")
                    # Size is full position amount
                    broker.place_order(signal, current_pos.amount)

        # 9. Paper Trading SL/TP Check (if simulated)
        if hasattr(broker, 'check_sl_tp') and not is_live:
            # Check against the latest candle (completed)
            # Or current ticker? Since we run on candle close, we check the JUST CLOSED candle
            # for any intra-bar SL/TP hits that we missed? 
            # Better: In paper mode, we assume we check every tick (or 5 mins).
            # Here we pass the latest candle.
            exit_sig = broker.check_sl_tp(current_candle)
            if exit_sig:
                 logger.info(f"Paper SL/TP Trigger: {exit_sig.reason}")
                 broker.place_order(exit_sig, current_pos.amount)

        # 10. Reporting
        # Simple logging status
        pnl_str = f"{current_pos.pnl:.2f}" if current_pos else "0.00"
        logger.info(f"Status: Bal: {current_bal:.2f} | Pos: {current_pos.side.value if current_pos else 'NONE'} (PnL: {pnl_str}) | Arm: {arm_idx}")


    # Single run or Loop
    if args.once:
        job()
    else:
        # Schedule: Run every X minutes to check? 
        # If timeframe is 1h, we should check shortly after the hour.
        # Or run every 1 minute and check if new candle arrived (by timestamp).
        
        logger.info("Starting Main Loop...")
        # Simple loop without 'schedule' lib for robustness in this snippet:
        # But user suggested 'schedule' lib.
        
        # run job immediately once
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
