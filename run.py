"""
Swingbot -- main entry point
-----------------------------
Scan cycle : every 10 minutes (configurable)
Strategy   : multi-symbol portfolio scan with scored setups
             RSI + EMA trend-following + breakout detection
             Long AND short signals
             Random Forest AI model (Phase 3)
"""
import argparse
import time
import threading
from datetime import datetime
import sys
import os

# Fix Windows console encoding for Arabic/Unicode output
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import yaml
import logging
from dotenv import load_dotenv

from core.utils import setup_logging, load_json
from core.clock import Clock
from core.types import Side, Reason, PositionStatus
from data.market import MarketData
from data.features import FeatureEngine
from storage.sqlite_store import SQLiteStore
from strategy.rsi_ema import RsiEmaStrategy
from strategy.regimes import RegimeDetector
from strategy.scanner import MarketScanner, MIN_SCORE
from risk.risk_engine import RiskEngine
from risk.circuit_breakers import CircuitBreaker
from execution.broker_paper import PaperBroker
from execution.broker_binance import BinanceBroker
from execution.broker_bybit import BybitBroker
from execution.broker_mexc import MexcBroker
from optimize.bandit import Bandit
from reports.daily_report import DailyReport
from optimize.param_sets import ARMS
from data.sentiment import SentimentEngine
from strategy.selector import SymbolSelector
from data.polymarket_client import PolymarketClient
from strategy.macro_filter import compute_macro_risk_scale
from signals.dump_btc import get_btc_risk_factor_for_symbol
from ml.model import SwingbotModel
from core.goal_tracker import GoalTracker
from core.notifier import Notifier
from core.trading_hours import is_good_time_to_trade
from risk.conservative_mode import ConservativeMode
from reports.weekly_report import WeeklyReport

# --- Config -------------------------------------------------------------------
load_dotenv()
with open("config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

from core.i18n import i18n

# --- Globals ------------------------------------------------------------------
store  = SQLiteStore(db_path=CONFIG['db_path'])
clock  = Clock(mode="live")
logger = logging.getLogger("swingbot")

SCAN_TOP_N = CONFIG.get('scan_top_n', 20)


# --- Entry Checklist ----------------------------------------------------------

def _passes_entry_checklist(
    macro_scale: float,
    sentiment_ok: bool,
    score: float,
    signal,
    circuit_breaker_ok: bool
) -> tuple:
    """
    Pre-flight checklist before any entry order.
    All conditions must pass. Returns (passed, reason_if_failed).
    """
    if not circuit_breaker_ok:
        return False, "Circuit breaker tripped"
    if macro_scale < 0.5:
        return False, f"Macro risk too high (scale={macro_scale:.2f})"
    if not sentiment_ok:
        return False, "Extreme fear -- sentiment gate blocked"
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


# --- Dashboard ----------------------------------------------------------------

def start_dashboard(config: dict, store_inst, state_dict,
                    notifier_inst=None, conservative_inst=None,
                    weekly_report_inst=None, goal_tracker_inst=None) -> None:
    """Start Flask dashboard in a background daemon thread."""
    if not config.get('dashboard', {}).get('enabled', True):
        return

    from dashboard.routes import create_app
    app = create_app(store=store_inst, state=state_dict, config=config)
    if app is None:
        return

    # Inject shared objects for dashboard routes
    app.config['notifier'] = notifier_inst
    app.config['conservative_mode'] = conservative_inst
    app.config['weekly_report'] = weekly_report_inst
    app.config['goal_tracker'] = goal_tracker_inst

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


# --- Observability ------------------------------------------------------------

def format_status_line(timestamp, symbol, price, signal, pos_state, arm, pnl, breaker, macro_status, next_check):
    tpl = i18n.get("STATUS_LINE")
    t_str = timestamp.strftime("%H:%M:%S")

    sig_key = f"SIGNAL_{signal}" if signal and signal != "-" else "SIGNAL_HOLD"
    sig_str = i18n.get(sig_key)

    pos_key = "POS_NONE"
    if pos_state == "OPEN": pos_key = "POS_OPEN"
    elif pos_state == "OPENING": pos_key = "POS_OPENING"
    elif pos_state == "CLOSING": pos_key = "POS_CLOSING"
    elif pos_state == "CLOSING_SLTP": pos_key = "POS_CLOSING"

    pos_str = i18n.get(pos_key)

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
        macro=macro_status,
        next_wait=next_check
    )


def main():
    parser = argparse.ArgumentParser(description="Swingbot")
    parser.add_argument("--paper", action="store_true", help="Run in paper mode (default)")
    parser.add_argument("--live",  action="store_true", help="Run in LIVE mode (DANGEROUS)")
    parser.add_argument("--once",  action="store_true", help="Run one cycle and exit")
    parser.add_argument("--fast",  action="store_true",
                        help="Run scan every 2 minutes (testing only)")
    parser.add_argument("--interval", type=int, default=None,
                        help="Scan interval in minutes (overrides config)")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Force a single trading pair (skips auto-scan)")
    parser.add_argument("--lang",   type=str, help="Language (en, ar)")
    parser.add_argument("--guide",  action="store_true",
                        help="Show bilingual help menu")
    args = parser.parse_args()

    # Language
    lang = args.lang or os.getenv("BOT_LANG") or CONFIG.get("lang", "ar")
    i18n.set_lang(lang)

    # Help
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

    # Scan interval
    if args.fast:
        CONFIG['scan_interval_minutes'] = 2
    elif args.interval:
        CONFIG['scan_interval_minutes'] = args.interval

    scan_interval_sec = CONFIG.get('scan_interval_minutes', 10) * 60

    # --- Three-gate live mode check -------------------------------------------
    check_live_env  = (os.getenv("TRADING_MODE", "paper").lower() == "live")
    check_live_file = os.path.exists("LIVE_OK.txt")
    check_live_conf = CONFIG.get("live", False)
    check_live_cfg_mode = (CONFIG.get("trading_mode", "paper").lower() == "live")

    is_live = False
    # Go live if all three safety gates pass (with --live flag OR config trading_mode)
    if args.live or check_live_cfg_mode:
        if check_live_env and check_live_file and check_live_conf:
            is_live = True
        else:
            print(f"\n!!!!!!!!!!!\n{i18n.get('WARNING_FORCE_PAPER')}\n!!!!!!!!!!!\n")
            if not check_live_env:  print("- env TRADING_MODE != live")
            if not check_live_file: print("- LIVE_OK.txt missing")
            if not check_live_conf: print("- config.yaml live != true")
            time.sleep(2)
            is_live = False

    clock.mode = "live" if is_live else "paper"

    # --- Logging --------------------------------------------------------------
    log_level_console = os.getenv("LOG_LEVEL_CONSOLE", "WARNING")
    log_level_file    = os.getenv("LOG_LEVEL_FILE", "DEBUG")
    setup_logging(console_level=log_level_console, file_level=log_level_file)

    # --- Components -----------------------------------------------------------
    primary_exchange = CONFIG.get('primary_exchange', 'bybit')
    market = MarketData(exchange_id=primary_exchange, sandbox=False)

    if is_live:
        if primary_exchange == 'bybit':
            account_type = CONFIG.get('bybit_account_type', 'spot')
            broker = BybitBroker(store, market, account_type=account_type)
        elif primary_exchange == 'binance':
            broker = BinanceBroker(store, market)
        elif primary_exchange == 'mexc':
            broker = MexcBroker(store, market)
        else:
            raise ValueError(f"Unknown exchange: {primary_exchange}")
    else:
        init_bal = CONFIG.get('paper_start_balance_usdt', 1000.0)
        broker = PaperBroker(store, clock, initial_balance=init_bal)

    # Scanner config
    scanner_conf = CONFIG.get('scanner', {})
    scanner_enabled = scanner_conf.get('enabled', False)
    max_positions = min(CONFIG.get('max_open_positions', 1), 5)  # Hard cap 5

    risk_engine = RiskEngine(
        total_capital=broker.get_balance(),
        risk_per_trade_percent=CONFIG['risk_per_trade_percent'],
        max_open_positions=max_positions,
        max_portfolio_risk_percent=CONFIG.get('max_portfolio_risk_percent', 5.0),
        max_single_position_percent=CONFIG.get('max_single_position_percent', 30.0),
    )

    circuit_breaker = CircuitBreaker(
        daily_loss_limit_percent=CONFIG['daily_loss_limit_percent'],
        consecutive_loss_limit=CONFIG['consecutive_loss_limit'],
        api_failure_limit=CONFIG['api_failure_limit']
    )

    strategy        = RsiEmaStrategy()
    scanner         = MarketScanner()
    bandit          = Bandit(store, exploration_prob=CONFIG['bandit']['exploration_prob'])
    reporter        = DailyReport(store)
    sentiment_engine = SentimentEngine()
    selector        = SymbolSelector(market.exchange, market=market)
    ml_model        = SwingbotModel()

    allow_short = CONFIG.get('allow_short', True)

    # Week 1 features
    notifier          = Notifier(CONFIG)
    conservative_mode = ConservativeMode(store, CONFIG)
    weekly_report     = WeeklyReport(store, CONFIG)
    goal_tracker      = GoalTracker(CONFIG, store)

    poly_client = None
    if CONFIG.get('polymarket', {}).get('enabled', False):
        poly_client = PolymarketClient()

    context = {
        "symbol": args.symbol or CONFIG.get('symbol', 'BTC/USDT'),
        "timeframe": CONFIG['timeframe'],
        "lookback": CONFIG['lookback']
    }

    timeframe = CONFIG['timeframe']
    lookback  = CONFIG['lookback']

    # --- State ----------------------------------------------------------------
    last_summary_date = datetime.utcnow().strftime('%Y-%m-%d')

    dashboard_state = {
        "positions_summary": [],
        "scan_results": [],
        "open_positions_count": 0,
        "last_cycle": None,
        "total_balance": broker.get_balance(),
        "daily_pnl": 0.0,
        "daily_pnl_pct": 0.0,
        "breaker_status": "OK",
        "sentiment_ok": True,
        "macro_scale": 1.0,
        "ai_confidence": None,
        "is_live": is_live,
    }

    # --- Dashboard start ------------------------------------------------------
    start_dashboard(CONFIG, store, dashboard_state,
                    notifier_inst=notifier,
                    conservative_inst=conservative_mode,
                    weekly_report_inst=weekly_report,
                    goal_tracker_inst=goal_tracker)

    # --- Startup Banner -------------------------------------------------------
    mode_str = i18n.get("MODE_LIVE") if is_live else i18n.get("MODE_PAPER")

    print("\n" + "="*50)
    print(i18n.get("START_MSG").format(mode=mode_str))
    print(i18n.get("BANNER_ACCOUNT").format(name=CONFIG.get('account_name', 'Unknown')))
    symbol_label = args.symbol or f"AUTO-SCAN top-{SCAN_TOP_N}"
    print(i18n.get("BANNER_SYMBOL").format(symbol=symbol_label, timeframe=timeframe))
    print(i18n.get("BANNER_RISK").format(
        risk=CONFIG['risk_per_trade_percent'],
        max_dd=CONFIG['daily_loss_limit_percent'],
        max_loss_run=CONFIG['consecutive_loss_limit']
    ))
    print(f"Exchange: {primary_exchange.upper()} | Scan interval: {CONFIG.get('scan_interval_minutes', 10)}m")
    print(f"Short selling: {'ENABLED' if allow_short else 'DISABLED'}")
    print(f"ML Model: {'LOADED' if ml_model.is_trained else 'NOT TRAINED (scanner-only mode)'}")

    if scanner_enabled:
        print(f"Scanner: ENABLED | Max Positions: {max_positions}")
    else:
        print(f"Scanner: DISABLED | Single-symbol mode: {context['symbol']}")

    if CONFIG.get('show_balances_on_startup', True):
        if is_live and hasattr(broker, 'get_detailed_balance'):
            try:
                bals = broker.get_detailed_balance()
                print(i18n.get("BANNER_BALANCE").format(
                    free=bals['USDT_free'],
                    total=bals['USDT_total'],
                    btc=bals.get('BTC_total', 0)
                ))
            except Exception as e:
                logger.error(f"Balance fetch failed: {e}")
        else:
            print(i18n.get("BANNER_PAPER_BAL").format(total=broker.get_balance()))
    print("="*50 + "\n")

    # --- Main cycle -----------------------------------------------------------
    def job():
        nonlocal last_summary_date
        cycle_start = time.time()

        # Reload config from file each cycle (dashboard changes apply immediately)
        try:
            with open("config.yaml", "r", encoding="utf-8") as f:
                fresh_config = yaml.safe_load(f)
            CONFIG.clear()
            CONFIG.update(fresh_config)
            notifier.config = CONFIG
            notifier.notif_config = CONFIG.get('notifications', {})
            conservative_mode.config = CONFIG
        except Exception:
            pass

        status = {
            "signal": None,
            "pos_state": "FLAT",
            "breaker": "OK",
            "active_symbol": context['symbol'],
            "price": 0.0,
            "arm": 0,
            "pnl": 0.0,
            "macro_prob": 0.0,
            "risk_scale": 1.0
        }

        try:
            logger.debug(f"=== Cycle Start: {datetime.utcnow()} ===")

            # -- Balance / P&L sync -------------------------------------------
            current_bal = broker.get_balance()
            risk_engine.total_capital = current_bal

            today_str   = datetime.utcnow().strftime('%Y-%m-%d')
            daily_stats = store.get_daily_stats(today_str)
            day_pnl     = daily_stats.get('pnl', 0.0)
            status['pnl'] = day_pnl

            # Track peak balance for compounding
            if CONFIG.get('peak_balance_tracking', True):
                store.update_peak_balance(today_str, current_bal)

            peak_balance = store.get_peak_balance()

            # Update dashboard state
            dashboard_state['total_balance'] = current_bal
            dashboard_state['daily_pnl'] = day_pnl
            dashboard_state['goal_tracker'] = goal_tracker.get_status(current_bal)
            start_bal = daily_stats.get('start_balance', current_bal)
            dashboard_state['daily_pnl_pct'] = (day_pnl / start_bal * 100) if start_bal else 0

            # Daily report rollover
            if last_summary_date != today_str:
                stats = store.get_daily_trade_stats(last_summary_date)
                header = i18n.get("SUMMARY_HEADER").format(date=last_summary_date)
                body = i18n.get("SUMMARY_STATS").format(
                    count=stats.get('count', 0),
                    winrate=stats.get('winrate', 0),
                    expectancy=stats.get('expectancy', 0),
                    pnl=stats.get('pnl', 0),
                    max_dd=0.0,
                    best_arm=stats.get('best_arm', '-')
                )
                sharpe = reporter.calculate_sharpe_ratio(last_summary_date)
                logger.warning(f"\n{header}\n{body}\nSharpe Ratio: {sharpe:.2f}\n")
                reporter.generate(last_summary_date)
                last_summary_date = today_str

            # -- Circuit Breaker -----------------------------------------------
            circuit_breaker_ok = True
            if daily_stats.get('paused_until'):
                status['breaker'] = f"PAUSED({daily_stats['paused_until']})"
                dashboard_state['breaker_status'] = status['breaker']
                logger.warning(f"[PAUSED] Trading halted until: {daily_stats['paused_until']}")
                circuit_breaker_ok = False
                return

            if day_pnl < -(current_bal * CONFIG['daily_loss_limit_percent'] / 100):
                logger.critical("Daily loss limit hit -- halting for the day.")
                store.update_daily_stats(today_str, {'paused_until': 'Next Day'})
                circuit_breaker_ok = False
                try:
                    notifier.notify_circuit_breaker(f"Daily loss: ${day_pnl:.2f}")
                except Exception:
                    pass
                return

            # -- Macro risk filter ---------------------------------------------
            if poly_client:
                pm_conf = CONFIG['polymarket']
                update_interval = pm_conf.get('update_hours', 6) * 3600
                last_snap = store.get_latest_polymarket_snapshot()

                need_update = True
                if last_snap and (time.time() - last_snap['timestamp']) < update_interval:
                    need_update = False
                    status['macro_prob'] = last_snap['probability']
                    status['risk_scale'] = last_snap['risk_scale']

                if need_update:
                    probs = [p for m in pm_conf.get('markets', [])
                             for p in [poly_client.get_probability(m)] if p is not None]
                    if probs:
                        risk_scale = compute_macro_risk_scale(probs)
                        macro_prob = sum(probs) / len(probs)
                        store.save_polymarket_snapshot(int(time.time()), "multi", macro_prob, risk_scale)
                        status['macro_prob'] = macro_prob
                        status['risk_scale'] = risk_scale
                    elif last_snap:
                        status['risk_scale'] = last_snap['risk_scale']
                        status['macro_prob'] = last_snap['probability']
                    else:
                        status['risk_scale'] = pm_conf.get('default_risk_scale_on_failure', 0.7)

            dashboard_state['macro_scale'] = status['risk_scale']

            # -- Sentiment gate ------------------------------------------------
            sentiment_ok = sentiment_engine.is_market_safe(
                threshold=CONFIG.get('sentiment_threshold', 20)
            )
            if not sentiment_ok:
                status['breaker'] = "SENTIMENT_FEAR"
                logger.warning("[SENTIMENT] Extreme fear detected -- no new entries this cycle.")

            dashboard_state['sentiment_ok'] = sentiment_ok
            dashboard_state['breaker_status'] = status['breaker']

            # -- Get all open positions ----------------------------------------
            open_positions = broker.get_open_positions()
            dashboard_state['open_positions_count'] = len(open_positions)
            dashboard_state['positions_summary'] = [
                {
                    "symbol": p.symbol,
                    "side": p.side.value,
                    "entry_price": p.entry_price,
                    "amount": p.amount,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "entry_time": p.entry_time,
                    "unrealized_pnl": 0.0,
                }
                for p in open_positions
            ]

            logger.warning(
                f"[CYCLE] {datetime.utcnow().strftime('%H:%M:%S')} UTC | "
                f"Balance: ${current_bal:.2f} | Open positions: {len(open_positions)} | "
                f"Day PnL: {day_pnl:+.2f} | Macro scale: {status['risk_scale']:.2f}"
            )

            # ==================================================================
            # PHASE A -- Process exits for every open position
            # ==================================================================
            for pos in list(open_positions):
                try:
                    sym = pos.symbol
                    candles = market.fetch_ohlcv(sym, timeframe, limit=lookback)
                    if not candles:
                        continue

                    df = FeatureEngine.compute_indicators(candles)
                    if df.empty:
                        continue

                    current_candle = candles[-1]
                    regime = RegimeDetector.detect(df.iloc[-1])

                    # Update unrealized PnL in dashboard
                    if pos.side == Side.BUY:
                        unrealized = (current_candle.close - pos.entry_price) * pos.amount
                    else:
                        unrealized = (pos.entry_price - current_candle.close) * pos.amount

                    for ps in dashboard_state['positions_summary']:
                        if ps['symbol'] == sym:
                            ps['unrealized_pnl'] = round(unrealized, 4)
                            ps['current_price'] = current_candle.close

                    arm_idx = bandit.select_arm_index()
                    params  = ARMS[arm_idx]

                    # Technical exit signal -- pass position object for short support
                    sig = strategy.check_signal(df, regime, params,
                                                current_position=pos, symbol=sym,
                                                allow_short=allow_short)

                    # Determine exit side based on position
                    exit_side = Side.SELL if pos.side == Side.BUY else Side.BUY

                    if sig and sig.side == exit_side:
                        logger.warning(f"[EXIT] {sym} | reason={sig.reason.value} | price={sig.price:.4f}")
                        order = broker.place_order(sig, pos.amount)

                        # Update trade outcome for ML
                        if order:
                            pnl = unrealized
                            pnl_pct = (pnl / (pos.entry_price * pos.amount)) * 100 if pos.entry_price and pos.amount else 0
                            hold_hours = (time.time() - pos.entry_time / 1000) / 3600 if pos.entry_time else 0
                            store.update_trade_outcome(
                                trade_id=pos.id,
                                outcome=1 if pnl > 0 else 0,
                                pnl=pnl,
                                pnl_pct=pnl_pct,
                                exit_reason=sig.reason.value,
                                hold_hours=hold_hours
                            )
                            # Notify exit
                            try:
                                notifier.notify_exit(sym, sig.reason.value, pnl, pnl_pct,
                                                     pos.entry_price, sig.price)
                            except Exception:
                                pass

                        status['pos_state'] = "CLOSING"
                        continue

                    # Paper SL/TP simulation
                    if not is_live:
                        exit_sig = broker.check_sl_tp(current_candle, symbol=sym)
                        if exit_sig:
                            logger.warning(f"[SL/TP] {sym} | reason={exit_sig.reason.value}")
                            order = broker.place_order(exit_sig, pos.amount)

                            if order:
                                if pos.side == Side.BUY:
                                    pnl = (exit_sig.price - pos.entry_price) * pos.amount
                                else:
                                    pnl = (pos.entry_price - exit_sig.price) * pos.amount
                                pnl_pct = (pnl / (pos.entry_price * pos.amount)) * 100 if pos.entry_price and pos.amount else 0
                                hold_hours = (time.time() - pos.entry_time / 1000) / 3600 if pos.entry_time else 0
                                store.update_trade_outcome(
                                    trade_id=pos.id,
                                    outcome=1 if pnl > 0 else 0,
                                    pnl=pnl,
                                    pnl_pct=pnl_pct,
                                    exit_reason=exit_sig.reason.value,
                                    hold_hours=hold_hours
                                )
                                # Notify SL/TP exit
                                try:
                                    notifier.notify_exit(sym, exit_sig.reason.value, pnl, pnl_pct,
                                                         pos.entry_price, exit_sig.price)
                                except Exception:
                                    pass

                            status['pos_state'] = "CLOSING_SLTP"

                except Exception as e:
                    logger.error(f"Exit check error for {pos.symbol}: {e}", exc_info=True)

            # ==================================================================
            # PHASE B -- Scan for new entry opportunities
            # ==================================================================
            open_positions = broker.get_open_positions()
            slots_available = risk_engine.max_open_positions - len(open_positions)

            if slots_available <= 0:
                logger.warning("[SCAN] All position slots filled -- skipping entry scan.")
                _log_cycle_summary(current_bal, day_pnl, len(open_positions), time.time() - cycle_start)
                _log_status(status, time.time() - cycle_start, scan_interval_sec)
                # Weekly report check + daily report
                weekly_report.check_and_send(notifier)
                return

            # -- Conservative mode check ---------------------------------------
            conservative, risk_mult, c_reason = conservative_mode.check(
                recent_trades=store.get_last_n_trades(10),
                day_pnl=day_pnl,
                daily_limit=CONFIG['daily_loss_limit_percent'],
                peak_balance=peak_balance,
                current_balance=current_bal
            )
            if conservative:
                logger.warning(f"[CONSERVATIVE] {c_reason} → risk x{risk_mult}")

            # Get universe of symbols to scan (always scan for dashboard display)
            if args.symbol:
                scan_candidates = [args.symbol]
            else:
                try:
                    scan_candidates = selector.get_top_pairs(
                        limit=SCAN_TOP_N,
                        min_volume_usdt=CONFIG.get('min_volume_usdt', 10_000_000)
                    )
                except Exception as e:
                    logger.error(f"Symbol selection failed: {e}")
                    circuit_breaker.record_api_error()
                    return

            logger.warning(f"[SCAN] Scanning {len(scan_candidates)} symbols for setups "
                           f"(need {slots_available} slot{'s' if slots_available > 1 else ''})...")

            # Score every candidate
            scored = []
            all_scanned = []  # All results for dashboard display
            already_held = {p.symbol for p in open_positions}

            for sym in scan_candidates:
                if sym in already_held:
                    continue
                try:
                    candles = market.fetch_ohlcv(sym, timeframe, limit=lookback)
                    if not candles:
                        continue
                    df = FeatureEngine.compute_indicators(candles)
                    if df.empty:
                        continue
                    regime = RegimeDetector.detect(df.iloc[-1])
                    score, breakout_detected = scanner.score_symbol(df, regime)

                    entry = {
                        'symbol':  sym,
                        'score':   score,
                        'df':      df,
                        'regime':  regime,
                        'candles': candles,
                        'price':   candles[-1].close,
                        'breakout_detected': breakout_detected,
                    }
                    all_scanned.append(entry)

                    if score >= MIN_SCORE:
                        scored.append(entry)
                        logger.debug(f"  {sym}: score={score:.1f} breakout={breakout_detected}")

                except Exception as e:
                    logger.error(f"Scan error for {sym}: {e}")

            scored.sort(key=lambda x: x['score'], reverse=True)
            all_scanned.sort(key=lambda x: x['score'], reverse=True)

            # Save scan results to dashboard (show top results even if below MIN_SCORE)
            display_results = scored if scored else all_scanned[:5]
            dashboard_state['scan_results'] = [
                {
                    "symbol": s['symbol'],
                    "score": s['score'],
                    "price": s['price'],
                    "signal": "BUY" if s['score'] >= MIN_SCORE else "--",
                }
                for s in display_results
            ]

            if scored:
                logger.warning(
                    f"[SCAN] {len(scored)} qualified setup(s). "
                    f"Top: {scored[0]['symbol']} ({scored[0]['score']:.0f}/100)"
                )
            else:
                logger.warning("[SCAN] No high-quality setups found this cycle.")

            # -- Trading hours filter (only blocks opening positions, not scanning) --
            hours_ok, hours_reason = is_good_time_to_trade(CONFIG)
            if not hours_ok:
                logger.warning(f"[HOURS] {hours_reason} — scan done, skipping entries.")
                _log_cycle_summary(current_bal, day_pnl, len(open_positions), time.time() - cycle_start)
                _log_status(status, time.time() - cycle_start, scan_interval_sec)
                weekly_report.check_and_send(notifier)
                return

            if not sentiment_ok:
                _log_cycle_summary(current_bal, day_pnl, len(open_positions), time.time() - cycle_start)
                _log_status(status, time.time() - cycle_start, scan_interval_sec)
                return

            # -- Open positions for top candidates -----------------------------
            entries_opened = 0
            for candidate in scored[:slots_available]:
                sym    = candidate['symbol']
                df     = candidate['df']
                regime = candidate['regime']
                cand_score = candidate['score']
                breakout_detected = candidate.get('breakout_detected', False)

                arm_idx = bandit.select_arm_index()
                params  = ARMS[arm_idx]

                sig = strategy.check_signal(df, regime, params,
                                            current_position=None, symbol=sym,
                                            allow_short=allow_short)

                # Entry checklist gate
                passed, reason = _passes_entry_checklist(
                    macro_scale=status.get('risk_scale', 1.0),
                    sentiment_ok=sentiment_ok,
                    score=cand_score,
                    signal=sig,
                    circuit_breaker_ok=circuit_breaker_ok
                )
                if not passed:
                    logger.warning(f"[SKIP] {sym}: {reason}")
                    continue

                # ML model gate
                ml_features = FeatureEngine.extract_ml_features(
                    df=df,
                    scanner_score=cand_score,
                    breakout_detected=breakout_detected,
                    macro_scale=status.get('risk_scale', 1.0),
                    fear_greed=sentiment_engine.get_score() if hasattr(sentiment_engine, 'get_score') else 50.0
                )

                enter, confidence, ml_reason = ml_model.should_enter(
                    ml_features, cand_score
                )

                if not enter:
                    logger.warning(f"[ML_SKIP] {sym}: {ml_reason}")
                    continue

                dashboard_state['ai_confidence'] = confidence

                # Dump BTC risk factor
                btc_factor = get_btc_risk_factor_for_symbol(sym, status, CONFIG)
                if btc_factor <= 0:
                    logger.info(f"Dump BTC blocked entry for {sym}")
                    continue

                # Reserved capital
                reserved = sum(p.entry_price * p.amount for p in open_positions)

                # Dynamic compounding risk
                base_balance = CONFIG.get('base_balance', 100.0)
                dynamic_risk = risk_engine.get_dynamic_risk_percent(
                    current_balance=current_bal,
                    base_balance=base_balance,
                    setup_score=cand_score,
                    peak_balance=peak_balance
                )

                # Risk sizing with dynamic risk
                base_size = risk_engine.calculate_position_size(
                    sig, reserved_capital=reserved, dynamic_risk_pct=dynamic_risk
                )
                size = base_size * status.get('risk_scale', 1.0) * btc_factor * (risk_mult if conservative else 1.0)

                # Breakout size multiplier
                if breakout_detected:
                    size *= 1.5
                    logger.warning(f"[BREAKOUT] {sym}: 1.5x size for breakout setup")

                # ML confidence boost
                if confidence >= 0.85:
                    size *= 1.5
                    logger.warning(f"[ML_BOOST] {sym}: {confidence:.0%} confidence -> 1.5x size")

                if size <= 0:
                    continue

                # Portfolio risk check
                ok, msg = risk_engine.can_open_position_for_symbol(
                    sym, open_positions, size, sig.price
                )
                if not ok:
                    logger.warning(f"[SKIP] {sym}: risk check failed -- {msg}")
                    continue

                market_struct = market.get_market_structure(sym)
                ok, msg = risk_engine.check_min_notional(size, sig.price, market_struct)
                if not ok:
                    logger.warning(f"[SKIP] {sym}: min-notional check failed -- {msg}")
                    continue

                side_str = "BUY" if sig.side == Side.BUY else "SELL(SHORT)"
                logger.warning(
                    f"[{side_str}] {sym} | score={cand_score:.0f} | "
                    f"price={sig.price:.4f} | size={size:.6f} | "
                    f"SL={sig.stop_loss:.4f} | TP={sig.take_profit:.4f} | "
                    f"risk={dynamic_risk:.1f}% | ML={confidence:.0%}"
                )
                order = broker.place_order(sig, size)
                if order:
                    # Save ML features for training
                    ml_features['trade_id'] = order.id
                    ml_features['symbol'] = sym
                    store.save_trade_features(ml_features)

                    # Notify entry
                    try:
                        notifier.notify_entry(sym, sig, size, cand_score, arm_idx)
                    except Exception:
                        pass

                    status['pos_state'] = "OPENING"
                    status['signal'] = sig.side.value
                    status['arm'] = arm_idx
                    open_positions = broker.get_open_positions()
                    entries_opened += 1

            # Update dashboard
            open_positions = broker.get_open_positions()
            dashboard_state['open_positions_count'] = len(open_positions)
            dashboard_state['last_cycle'] = datetime.utcnow().isoformat()

            if open_positions:
                status['pos_state'] = "OPEN"
                status['active_symbol'] = ", ".join(p.symbol for p in open_positions)

            _log_cycle_summary(broker.get_balance(), day_pnl,
                               len(open_positions),
                               time.time() - cycle_start,
                               entries_opened=entries_opened)
            _log_status(status, time.time() - cycle_start, scan_interval_sec)

            if scanner_enabled and open_positions:
                logger.warning(f"  Open Positions ({len(open_positions)}/{max_positions}): " +
                             ", ".join(f"{p.symbol}@{p.entry_price:.2f}" for p in open_positions))

            # Weekly report check
            try:
                weekly_report.check_and_send(notifier)
            except Exception as e:
                logger.error(f"Weekly report check failed: {e}")

            # Daily report (at configured hour)
            try:
                report_hour = CONFIG.get('notifications', {}).get('daily_report_hour_utc', 8)
                now_utc = datetime.utcnow()
                if now_utc.hour == report_hour and now_utc.minute < 11:
                    stats = store.get_daily_trade_stats(today_str)
                    if stats.get('count', 0) > 0:
                        notifier.notify_daily_report(stats)
            except Exception as e:
                logger.error(f"Daily report failed: {e}")

        except Exception as e:
            logger.error(f"Cycle Error: {e}", exc_info=True)
            circuit_breaker.record_api_error()

    # --- Helpers --------------------------------------------------------------
    def _log_cycle_summary(balance, pnl, open_pos, elapsed, entries_opened=0):
        next_scan = max(0, int(scan_interval_sec - elapsed))
        m, s = divmod(next_scan, 60)
        logger.warning(
            f"[DONE] Balance: ${balance:.2f} | Open: {open_pos} pos | "
            f"Day PnL: {pnl:+.2f} | Entries: {entries_opened} | "
            f"Next scan in: {m}m {s}s"
        )

    def _log_status(status, elapsed, interval):
        next_wait = max(0, int(interval - elapsed))
        if status.get('price', 0) == 0 and status.get('active_symbol'):
            status['price'] = '-'
        try:
            line = format_status_line(
                datetime.utcnow(),
                status['active_symbol'],
                status['price'],
                status['signal'],
                status['pos_state'],
                status['arm'],
                status['pnl'],
                status['breaker'],
                i18n.get("MACRO_STATUS").format(p=status.get('macro_prob', 0), sc=status.get('risk_scale', 1)),
                next_wait
            )
            logger.warning(line)
        except Exception:
            pass

    # --- Run ------------------------------------------------------------------
    if args.once:
        job()
    else:
        interval_min = CONFIG.get('scan_interval_minutes', 10)
        logger.warning(f"Starting Swingbot -- {interval_min}-minute scan cycle. Press Ctrl+C to stop.")
        job()  # Run immediately on start
        while True:
            time.sleep(scan_interval_sec)
            job()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping...")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal Error: {e}")
        sys.exit(1)
