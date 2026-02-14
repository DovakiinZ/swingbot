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

from core.i18n import i18n

# Global State
store = SQLiteStore(db_path=CONFIG['db_path'])
clock = Clock(mode="live") 
logger = logging.getLogger("swingbot")

# Observability Helpers
def format_status_line(timestamp, symbol, price, signal, pos_state, arm, pnl, breaker, next_check):
    # Retrieve template
    tpl = i18n.get("STATUS_LINE")
    t_str = timestamp.strftime("%H:%M:%S")
    
    # Translate values
    sig_key = f"SIGNAL_{signal}" if signal and signal != "-" else "SIGNAL_HOLD"
    sig_str = i18n.get(sig_key)
    
    # Map pos_state to key
    pos_key = "POS_NONE"
    if pos_state == "OPEN": pos_key = "POS_OPEN"
    elif pos_state == "OPENING": pos_key = "POS_OPENING"
    elif pos_state == "CLOSING": pos_key = "POS_CLOSING"
    elif pos_state == "CLOSING_SLTP": pos_key = "POS_CLOSING"
    
    pos_str = i18n.get(pos_key)
    
    # Breaker translation
    breaker_str = i18n.get("BREAKER_OK")
    if "PAUSED" in breaker:
        if "LOSS_LIMIT" in breaker: breaker_str = f"{i18n.get('BREAKER_PAUSED')}({i18n.get('BREAKER_LOSS_LIMIT')})"
        elif "SENTIMENT" in breaker: breaker_str = f"{i18n.get('BREAKER_PAUSED')}({i18n.get('BREAKER_SENTIMENT')})"
        else: breaker_str = f"{i18n.get('BREAKER_PAUSED')}"
    elif "SENTIMENT_FEAR" in breaker:
        breaker_str = i18n.get("BREAKER_SENTIMENT")

    return tpl.format(
        timestamp=t_str,
        symbol=symbol,
        price=price,
        signal=sig_str,
        pos_state=pos_str,
        arm=arm,
        pnl=pnl,
        breaker=breaker_str,
        next_wait=next_check
    )

def main():
    parser = argparse.ArgumentParser(description="Swingbot")
    parser.add_argument("--paper", action="store_true", help="Run in paper mode (default)")
    parser.add_argument("--live", action="store_true", help="Run in LIVE mode (DANGEROUS)")
    parser.add_argument("--once", action="store_true", help="Run loop once and exit")
    parser.add_argument("--symbol", type=str, default=CONFIG.get('symbol', 'BTC/USDT'), help="Trading pair")
    parser.add_argument("--lang", type=str, help="Language (en, ar)")
    parser.add_argument("--guide", action="store_true", help="Show bilingual help menu / عرض القائمة")
    args = parser.parse_args()

    # Resolve Language
    lang = args.lang or os.getenv("BOT_LANG") or CONFIG.get("lang", "ar")
    i18n.set_lang(lang)

    # 0. Help Guide
    if args.guide:
        print("\n" + i18n.get("HELP_TITLE"))
        print(i18n.get("HELP_USAGE"))
        print(i18n.get("HELP_Paper"))
        print(i18n.get("HELP_Live"))
        print(i18n.get("HELP_Once"))
        print(i18n.get("HELP_Lang"))
        print(i18n.get("HELP_Guide"))
        print(i18n.get("HELP_Desc"))
        sys.exit(0)

    # 1. Mode Selection Logic (Strict)
    check_live_env = (os.getenv("TRADING_MODE", "paper").lower() == "live")
    check_live_file = os.path.exists("LIVE_OK.txt")
    check_live_conf = CONFIG.get("live", False)
    
    is_live = False
    
    if args.live:
        # User requested LIVE via CLI. Check gates.
        if check_live_env and check_live_file and check_live_conf:
            is_live = True
        else:
            # Fallback
            print(f"\n!!!!!!!!!!!\n{i18n.get('WARNING_FORCE_PAPER')}\n!!!!!!!!!!!\n")
            if not check_live_env: print("- env TRADING_MODE != live")
            if not check_live_file: print("- LIVE_OK.txt missing")
            if not check_live_conf: print("- config.yaml live != true")
            time.sleep(2)
            is_live = False
            
    clock.mode = "live" if is_live else "paper"

    # Setup Logging
    log_level_console = os.getenv("LOG_LEVEL_CONSOLE", "WARNING")
    log_level_file = os.getenv("LOG_LEVEL_FILE", "DEBUG") 
    setup_logging(console_level=log_level_console, file_level=log_level_file)
    
    # Initialize Components
    market = MarketData(sandbox=False) 
    
    if is_live:
        broker = BinanceBroker(store, market)
    else:
        init_bal = CONFIG.get('paper_start_balance_usdt', 1000.0)
        broker = PaperBroker(store, clock, initial_balance=init_bal)

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
    
    # --- STARTUP BANNER ---
    mode_str = i18n.get("MODE_LIVE") if is_live else i18n.get("MODE_PAPER")
    print("\n" + "="*40)
    print(i18n.get("START_MSG").format(mode=mode_str))
    print(i18n.get("BANNER_ACCOUNT").format(name=CONFIG.get('account_name', 'Unknown')))
    print(i18n.get("BANNER_SYMBOL").format(symbol=context['symbol'], timeframe=context['timeframe']))
    print(i18n.get("BANNER_RISK").format(
        risk=CONFIG['risk_per_trade_percent'], 
        max_dd=CONFIG['daily_loss_limit_percent'],
        max_loss_run=CONFIG['consecutive_loss_limit']
    ))
    
    if CONFIG.get('show_balances_on_startup', True):
        if is_live and hasattr(broker, 'get_detailed_balance'):
            try:
                bals = broker.get_detailed_balance()
                print(i18n.get("BANNER_BALANCE").format(
                    free=bals['USDT_free'], 
                    total=bals['USDT_total'], 
                    btc=bals['BTC_total']
                ))
            except Exception as e:
                 logger.error(f"Balance fetch failed: {e}")
                 print(i18n.get("BANNER_BALANCE").format(free=0, total=0, btc=0) + " (Error)")
        else:
             print(i18n.get("BANNER_PAPER_BAL").format(total=broker.get_balance()))
    print("="*40 + "\n")
    
    
    # State tracking
    last_summary_date = datetime.utcnow().strftime('%Y-%m-%d')

    # Job Loop
    def job():
        nonlocal last_summary_date
        cycle_start_time = time.time()
        
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
            
            # State Sync
            current_bal = broker.get_balance()
            risk_engine.total_capital = current_bal
            
            today_str = datetime.utcnow().strftime('%Y-%m-%d')
            daily_stats = store.get_daily_stats(today_str)
            status['pnl'] = daily_stats.get('pnl', 0.0)

            # Daily Summary Logic (New Day)
            if last_summary_date != today_str:
                # Calculate stats for the closed day
                stats = store.get_daily_trade_stats(last_summary_date)
                
                header = i18n.get("SUMMARY_HEADER").format(date=last_summary_date)
                # Need to add start_balance to calc max_dd % properly in store, but approximating...
                body = i18n.get("SUMMARY_STATS").format(
                    count=stats['count'],
                    winrate=stats['winrate'],
                    expectancy=stats['expectancy'],
                    pnl=stats['pnl'],
                    max_dd=0.0, # stats['max_dd'] is abs amount, % needs equity context
                    best_arm=stats['best_arm']
                )
                logger.warning(f"\n{header}")
                logger.warning(body)
                logger.warning("===================================\n")
                
                # Write Report
                reporter.generate_report(last_summary_date)
                last_summary_date = today_str

            # 1. Circuit Breaker
            if daily_stats.get('paused_until'):
                status['breaker'] = f"PAUSED({daily_stats['paused_until']})"
                # Loop must continue to print status
                logger.warning(format_status_line(datetime.utcnow(), status['active_symbol'], 0, "-", "POS_NONE", "-", status['pnl'], status['breaker'], 300))
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
                status['arm'] = 0 
                # Try to load arm_id if we enhance .get_open_position to return it
            else:
                if not args.symbol: 
                    top_pairs = selector.get_top_pairs(limit=1)
                    if top_pairs:
                        status['active_symbol'] = top_pairs[0]
            
            # 3. Fetch Data
            candles = market.fetch_ohlcv(status['active_symbol'], context['timeframe'], limit=context['lookback'])
            if not candles:
                logger.debug("No candles fetched.")
                circuit_breaker.record_api_error()
                return

            # 4. Features
            df = FeatureEngine.compute_indicators(candles)
            if df.empty: return
            
            current_candle = candles[-1]
            status['price'] = current_candle.close
            regime = RegimeDetector.detect(df.iloc[-1])
            
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
                if signal.side == Side.BUY:
                    if current_pos: pass
                    elif not safe_to_enter: pass
                    elif not risk_engine.can_open_new_position(0): pass
                    else:
                        size = risk_engine.calculate_position_size(signal)
                        market_struct = market.get_market_structure(status['active_symbol'])
                        ok, msg = risk_engine.check_min_notional(size, signal.price, market_struct)
                        if ok:
                            logger.info(f"EXECUTING BUY: {size} @ {signal.price}")
                            broker.place_order(signal, size)
                            status['pos_state'] = "OPENING"
                            # Record arm used for this trade logic here ideally
                            # For MVP we rely on post-trade matching or future enhancement

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

            # 10. Status Log
            elapsed = time.time() - cycle_start_time
            next_wait = 300 - int(elapsed) 
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
