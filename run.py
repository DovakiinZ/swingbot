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
import queue
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
from strategy.scanner import MarketScanner
from strategy.signal_scorer import SignalScorer
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
from ml.triple_barrier import TripleBarrierLabeler, BarrierConfig
from core.goal_tracker import GoalTracker
from core.notifier import Notifier
from core.health_monitor import HealthMonitor
from core.trading_hours import is_good_time_to_trade
from risk.conservative_mode import ConservativeMode
from reports.weekly_report import WeeklyReport
from strategy.momentum_breakout import MomentumBreakoutStrategy

# WebSocket monitor — optional dependency
try:
    from data.websocket_monitor import WebSocketMonitor, WS_AVAILABLE
except ImportError:
    WS_AVAILABLE = False

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
MIN_SCORE  = CONFIG.get('min_score', 55)   # Read from config, not hardcoded


# --- Entry Checklist ----------------------------------------------------------

def _passes_entry_checklist(
    macro_scale: float,
    sentiment_ok: bool,
    score: float,
    signal,
    circuit_breaker_ok: bool,
    sniper_mode: bool = False
) -> tuple:
    """
    Pre-flight checklist before any entry order.
    All conditions must pass. Returns (passed, reason_if_failed).

    Sniper mode: bypasses macro/sentiment gates but keeps circuit breakers,
    score gate (85+), and R:R check active. Only perfect setups get through.
    """
    # Circuit breakers ALWAYS active -- even in sniper mode
    if not circuit_breaker_ok:
        return False, "Circuit breaker tripped"

    if sniper_mode:
        # Sniper: skip macro and sentiment, trust the setup
        pass
    else:
        if macro_scale < 0.5:
            return False, f"Macro risk too high (scale={macro_scale:.2f})"
        if not sentiment_ok:
            return False, "Extreme fear -- sentiment gate blocked"

    min_score = CONFIG.get('min_score', 65)
    if score < min_score:
        return False, f"Score too low ({score:.0f} < {min_score})"
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
    parser.add_argument("--sniper", action="store_true",
                        help="Sniper mode: ignore macro/sentiment, only trade 85+ score setups")
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

    # Sniper mode: bypass macro/sentiment, only trade 85+ score setups
    if args.sniper:
        CONFIG['strategy_mode'] = 'sniper'
        CONFIG['min_score'] = 85
        logger.warning("[SNIPER] Mode activated -- macro/sentiment bypassed, min_score=85")

    SNIPER_MODE = CONFIG.get('strategy_mode', 'normal') == 'sniper'

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
    # Exchange architecture:
    #   trading_exchange   → where orders are placed (MEXC, user account)
    #   market_data_exchange → where we READ prices/candles/funding (Bybit default)
    #   Bybit + Binance used as read-only market indicators via public API only
    trading_exchange    = CONFIG.get('trading_exchange', 'mexc')
    data_exchange       = CONFIG.get('market_data_exchange', 'bybit')

    market      = MarketData(exchange_id=data_exchange, sandbox=False)   # data source
    market_mexc = MarketData(exchange_id='mexc', sandbox=False)          # MEXC symbol check

    if is_live:
        if trading_exchange == 'mexc':
            broker = MexcBroker(store, market_mexc)
        elif trading_exchange == 'bybit':
            account_type = CONFIG.get('bybit_account_type', 'spot')
            broker = BybitBroker(store, market, account_type=account_type)
        elif trading_exchange == 'binance':
            broker = BinanceBroker(store, market)
        else:
            raise ValueError(f"Unknown trading exchange: {trading_exchange}")
    else:
        init_bal = CONFIG.get('paper_start_balance_usdt', 1000.0)
        broker = PaperBroker(
            store, clock, initial_balance=init_bal,
            trail_activate_pct=CONFIG.get('trailing_stop_activate_pct', 0.01),
            trail_pct=CONFIG.get('trailing_stop_trail_pct', 0.008)
        )

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

    strategy        = RsiEmaStrategy(tb_config=CONFIG.get('triple_barrier'))
    scanner         = MarketScanner()
    signal_scorer   = SignalScorer(threshold=CONFIG.get('signal_score_threshold', 70))
    bandit          = Bandit(store, exploration_prob=CONFIG['bandit']['exploration_prob'])
    reporter        = DailyReport(store)
    sentiment_engine = SentimentEngine()
    selector        = SymbolSelector(market.exchange, market=market)   # uses data exchange (Bybit)
    ml_model        = SwingbotModel()

    # Triple-Barrier labeler for richer training data
    tb_conf = CONFIG.get('triple_barrier', {})
    tb_labeler = None
    if tb_conf.get('enabled', False):
        tb_labeler = TripleBarrierLabeler(BarrierConfig(
            upper_multiplier=tb_conf.get('upper_multiplier', 2.0),
            lower_multiplier=tb_conf.get('lower_multiplier', 1.0),
            max_holding_hours=tb_conf.get('max_holding_hours', 48)
        ))

    allow_short = CONFIG.get('allow_short', True)

    # Week 1 features
    notifier          = Notifier(CONFIG)
    conservative_mode = ConservativeMode(store, CONFIG)
    weekly_report     = WeeklyReport(store, CONFIG)
    goal_tracker      = GoalTracker(CONFIG, store)

    # Health monitor — background thread, 60s checks, Discord alerts
    health_monitor = HealthMonitor(
        config=CONFIG, broker=broker, store=store,
        notifier=notifier, circuit_breaker=circuit_breaker, market=market
    )
    health_monitor.start()

    # -- Momentum Strategy -------------------------------------------------
    momentum_strategy = MomentumBreakoutStrategy(
        min_price_change=CONFIG.get('momentum_min_change', 0.003),
        min_volume_ratio=CONFIG.get('momentum_min_volume', 2.0),
        sl_atr_mult=CONFIG.get('momentum_sl_mult', 1.5),
        tp_atr_mult=CONFIG.get('momentum_tp_mult', 2.5),
        cooldown_seconds=CONFIG.get('momentum_cooldown_sec', 300),
    )

    # -- Momentum signal queue (thread-safe) --------------------------------
    momentum_queue = queue.Queue(maxsize=50)

    def on_momentum_detected(symbol, direction, price,
                              price_change_pct, volume_ratio, timestamp):
        """Called from WebSocket thread — puts signal in queue for main thread."""
        try:
            momentum_queue.put_nowait({
                'symbol':           symbol,
                'direction':        direction,
                'price':            price,
                'price_change_pct': price_change_pct,
                'volume_ratio':     volume_ratio,
                'timestamp':        timestamp,
            })
        except queue.Full:
            pass   # Drop if queue full — never block WebSocket thread

    # -- Start WebSocket monitor -------------------------------------------
    ws_monitor = None
    ws_config = CONFIG.get('websocket', {})
    if WS_AVAILABLE and ws_config.get('enabled', True):
        initial_symbols = ws_config.get(
            'symbols',
            ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT']
        )
        ws_monitor = WebSocketMonitor(
            symbols=initial_symbols,
            on_momentum=on_momentum_detected,
            momentum_threshold=CONFIG.get('momentum_min_change', 0.003),
            volume_multiplier=CONFIG.get('momentum_min_volume', 2.0),
        )
        ws_monitor.start()
        logger.warning(f"[WS] Monitoring {len(initial_symbols)} symbols in real-time")

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
        "websocket": {
            "connected": False,
            "symbols_monitored": 0,
            "momentum_signals_today": 0,
            "last_momentum": "—",
        },
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
    print(f"Trading: {trading_exchange.upper()} | Data: {data_exchange.upper()} | Scan: {CONFIG.get('scan_interval_minutes', 10)}m")
    print(f"Short selling: {'ENABLED' if allow_short else 'DISABLED'}")
    print(f"ML Model: {'LOADED' if ml_model.is_trained else 'NOT TRAINED (scanner-only mode)'}")
    print(f"Triple-Barrier: {'ENABLED' if tb_labeler else 'DISABLED'}")
    print(f"WebSocket Momentum: {'ENABLED' if ws_monitor else 'DISABLED'}")

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

    # --- Triple-Barrier labeling on trade close ------------------------------
    def label_closed_trade(pos):
        """Fetch candles since entry, apply TB labeling, save to DB."""
        if not tb_labeler:
            return
        try:
            candles_data = market.fetch_ohlcv(pos.symbol, timeframe, limit=lookback)
            if not candles_data:
                return
            df_tb = FeatureEngine.compute_indicators(candles_data)
            if df_tb.empty:
                return

            # Find candles after entry time
            import pandas as _pd
            entry_ts = pos.entry_time
            future = df_tb[df_tb.index > _pd.Timestamp(entry_ts, unit='ms')]
            if future.empty:
                return

            # ATR at entry
            before_entry = df_tb[df_tb.index <= _pd.Timestamp(entry_ts, unit='ms')]
            atr_at_entry = before_entry.iloc[-1].get('atr', 0) if not before_entry.empty else 0
            if atr_at_entry <= 0:
                return

            tb_result = tb_labeler.label_trade(
                entry_price=pos.entry_price,
                candles_after_entry=future,
                atr_at_entry=atr_at_entry,
                side=pos.side.value
            )

            store.update_trade_barrier_label(pos.id, {
                'tb_label':            tb_result.label,
                'tb_hours_to_barrier': tb_result.hours_to_barrier,
                'tb_barrier_hit':      tb_result.barrier_hit,
                'tb_upper_barrier':    tb_result.upper_barrier,
                'tb_lower_barrier':    tb_result.lower_barrier,
                'tb_return_pct':       tb_result.return_pct,
            })

            logger.info(
                f"[TB] {pos.symbol}: label={tb_result.label:+d} | "
                f"hit={tb_result.barrier_hit} | "
                f"hours={tb_result.hours_to_barrier:.1f} | "
                f"return={tb_result.return_pct:.2%}"
            )
        except Exception as e:
            logger.warning(f"[TB] Labeling failed for {pos.symbol}: {e}")

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

        # Re-check sniper mode from config (dashboard toggle)
        nonlocal SNIPER_MODE
        SNIPER_MODE = CONFIG.get('strategy_mode', 'normal') == 'sniper'

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
                logger.critical("Daily loss limit hit (%) -- halting for the day.")
                store.update_daily_stats(today_str, {'paused_until': 'Next Day'})
                circuit_breaker_ok = False
                try:
                    notifier.notify_circuit_breaker(f"Daily loss: ${day_pnl:.2f}")
                except Exception:
                    pass
                return

            # FIX 7: Hard dollar daily loss limit
            max_daily_loss_usd = CONFIG.get('max_daily_loss_usd', 15.0)
            if day_pnl < -max_daily_loss_usd:
                logger.critical(
                    f"[HARD STOP] Daily loss ${abs(day_pnl):.2f} exceeds "
                    f"${max_daily_loss_usd:.2f} limit -- halting for the day."
                )
                store.update_daily_stats(today_str, {'paused_until': 'Next Day'})
                circuit_breaker_ok = False
                try:
                    notifier.notify_circuit_breaker(f"Hard daily loss limit: ${day_pnl:.2f} / -${max_daily_loss_usd:.2f}")
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
            dashboard_state['sniper_mode'] = SNIPER_MODE
            dashboard_state['breaker_status'] = status['breaker']

            # Update WebSocket status in dashboard
            if ws_monitor:
                dashboard_state['websocket'] = ws_monitor.get_status()

            # ==================================================================
            # PHASE 0 — Process momentum signals from WebSocket (PRIORITY)
            # These fire immediately — don't wait for the 10-min cycle
            # ==================================================================
            momentum_signals_processed = 0
            _momentum_opened_this_cycle = set()  # Prevent duplicate opens within same batch

            while not momentum_queue.empty() and momentum_signals_processed < 5:
                try:
                    event = momentum_queue.get_nowait()
                    momentum_signals_processed += 1

                    sym = event['symbol']

                    # FIX 1: Strict duplicate position guard
                    # Check both DB positions AND symbols opened earlier in this cycle
                    current_open = broker.get_open_positions()
                    if any(p.symbol == sym for p in current_open):
                        logger.warning(f"[MOMENTUM SKIP] {sym}: already have open position (duplicate blocked)")
                        continue
                    if sym in _momentum_opened_this_cycle:
                        logger.warning(f"[MOMENTUM SKIP] {sym}: already opened this cycle (duplicate blocked)")
                        continue

                    # Skip if slots full
                    if len(current_open) >= risk_engine.max_open_positions:
                        continue

                    # Circuit breaker check
                    if not circuit_breaker_ok:
                        continue

                    # Daily loss limit check
                    if day_pnl < -(current_bal * CONFIG['daily_loss_limit_percent'] / 100):
                        continue

                    # Get current ATR for this symbol
                    try:
                        candles = market.fetch_ohlcv(sym, CONFIG['timeframe'], limit=50)
                        if not candles:
                            continue
                        df = FeatureEngine.compute_indicators(candles)
                        if df.empty:
                            continue
                        atr = float(df.iloc[-1].get('atr', event['price'] * 0.01))
                        atr_pct = float(df.iloc[-1].get('atr_percent', 1.0))
                    except Exception:
                        atr     = event['price'] * 0.01
                        atr_pct = 1.0

                    # Generate momentum signal
                    sig = momentum_strategy.process_momentum_event(
                        symbol=sym,
                        direction=event['direction'],
                        price=event['price'],
                        price_change_pct=event['price_change_pct'],
                        volume_ratio=event['volume_ratio'],
                        atr=atr,
                        atr_pct=atr_pct,
                        timestamp=event['timestamp'],
                        allow_short=CONFIG.get('allow_short', True)
                    )

                    if not sig:
                        continue

                    # FIX 4: R:R gate for momentum signals too
                    if sig.stop_loss and sig.price and sig.take_profit:
                        sl_dist = abs(sig.price - sig.stop_loss)
                        tp_dist = abs(sig.price - sig.take_profit)
                        rr = tp_dist / sl_dist if sl_dist > 0 else 0
                        min_rr = CONFIG.get('min_rr_ratio', 2.0)
                        if rr < min_rr:
                            logger.warning(f"[MOMENTUM SKIP] {sym}: SKIPPED — RR ratio {rr:.2f} below minimum {min_rr}")
                            continue

                    # FIX 5: Volume confirmation for momentum signals
                    vol_ratio = df.iloc[-1].get('volume_ratio', 1.0) if not df.empty else 1.0
                    vol_mult = CONFIG.get('volume_multiplier', 1.2)
                    if vol_ratio < vol_mult * 0.95:  # 5% tolerance for floating point
                        logger.warning(f"[MOMENTUM SKIP] {sym}: SKIPPED — volume too low ({vol_ratio:.1f}x < {vol_mult}x)")
                        continue

                    # Size the position
                    confidence = sig.strength
                    reserved   = sum(p.entry_price * p.amount for p in current_open)

                    # Dynamic compounding risk for momentum trades
                    base_balance = CONFIG.get('base_balance', 100.0)
                    mom_score = momentum_strategy.calculate_confidence_score(
                        event['price_change_pct'], event['volume_ratio']
                    )
                    dynamic_risk = risk_engine.get_dynamic_risk_percent(
                        current_balance=current_bal,
                        base_balance=base_balance,
                        setup_score=mom_score,
                        peak_balance=peak_balance
                    )

                    base_size = risk_engine.calculate_position_size(
                        sig, reserved_capital=reserved, dynamic_risk_pct=dynamic_risk
                    )

                    if base_size <= 0:
                        continue

                    # Notional check
                    market_struct = market.get_market_structure(sym)
                    ok_notional, msg = risk_engine.check_min_notional(base_size, sig.price, market_struct)
                    if not ok_notional:
                        logger.warning(f"[MOMENTUM SKIP] {sym}: {msg}")
                        continue

                    # Portfolio risk check
                    ok, msg = risk_engine.can_open_position_for_symbol(
                        sym, current_open, base_size, sig.price
                    )
                    if not ok:
                        logger.warning(f"[MOMENTUM SKIP] {sym}: {msg}")
                        continue

                    # EXECUTE
                    side_str = "BUY" if sig.side == Side.BUY else "SELL(SHORT)"
                    logger.warning(
                        f"[MOMENTUM {side_str}] {sym} | "
                        f"price={sig.price:.4f} | size={base_size:.6f} | "
                        f"change={event['price_change_pct']:+.3%} | "
                        f"vol={event['volume_ratio']:.1f}x | "
                        f"risk={dynamic_risk:.1f}%"
                    )
                    order = broker.place_order(sig, base_size)
                    if order:
                        _momentum_opened_this_cycle.add(sym)  # FIX 1: Mark as opened
                        # Save ML features for training
                        try:
                            ml_features = FeatureEngine.extract_ml_features(
                                df=df,
                                scanner_score=mom_score,
                                breakout_detected=False,
                                macro_scale=status.get('risk_scale', 1.0),
                                fear_greed=sentiment_engine.get_score() if hasattr(sentiment_engine, 'get_score') else 50.0
                            )
                            ml_features['trade_id'] = order.id
                            ml_features['symbol'] = sym
                            store.save_trade_features(ml_features)
                        except Exception:
                            pass

                        try:
                            notifier.notify_entry(sym, sig, base_size, mom_score, 0,
                                                  atr=float(df.iloc[-1].get('atr', 0)) if not df.empty else 0)
                        except Exception:
                            pass
                        status['pos_state'] = "OPENING"

                except Exception as e:
                    logger.error(f"[MOMENTUM] Processing error: {e}", exc_info=True)

            if momentum_signals_processed > 0:
                logger.warning(f"[MOMENTUM] Processed {momentum_signals_processed} momentum signal(s)")

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
                            # Triple-Barrier labeling
                            label_closed_trade(pos)
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
                                # Triple-Barrier labeling
                                label_closed_trade(pos)
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

            for scan_idx, sym in enumerate(scan_candidates):
                if sym in already_held:
                    continue
                # Rate limit: small delay between API calls to avoid Bybit 429
                if scan_idx > 0 and scan_idx % 5 == 0:
                    time.sleep(1.0)
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
                        logger.debug(f"  {sym}: score={score:.1f} breakout={breakout_detected} regime={regime.value}")

                except Exception as e:
                    logger.error(f"Scan error for {sym}: {e}")

            scored.sort(key=lambda x: x['score'], reverse=True)
            all_scanned.sort(key=lambda x: x['score'], reverse=True)

            # Update WebSocket symbols after scan
            if ws_monitor and all_scanned:
                top_ws_symbols = [s['symbol'] for s in all_scanned[:10]]
                ws_monitor.update_symbols(top_ws_symbols)
                logger.debug(f"[WS] Updated monitoring: {top_ws_symbols[:5]}...")

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
            htf_timeframe  = CONFIG.get('htf_timeframe', '4h')
            htf_ema_period = CONFIG.get('htf_ema_period', 200)
            mtf_enabled    = CONFIG.get('mtf_filter_enabled', True)
            funding_filter = CONFIG.get('funding_rate_filter', True)
            funding_long_block  = CONFIG.get('funding_long_block', 0.0008)   # 0.08%/8h
            funding_short_block = CONFIG.get('funding_short_block', -0.0005) # -0.05%/8h

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

                # -- Signal confidence scorer gate ----------------------------
                if sig is not None:
                    scorer_result = signal_scorer.score(df, regime, symbol=sym)
                    if not scorer_result['passed']:
                        logger.warning(f"[SCORER_SKIP] {sym}: score {scorer_result['total']}/100 < {signal_scorer.threshold}")
                        continue

                # -- 4H Multi-Timeframe confluence filter ----------------------
                # Research: HTF filter raises Profit Factor from ~1.4 to ~2.0+
                # Only take 1H longs when 4H trend is up, shorts when 4H is down
                if mtf_enabled and sig is not None:
                    try:
                        htf = market.fetch_htf_trend(sym, htf_timeframe, htf_ema_period)
                        htf_trend = htf.get('trend', 'flat')
                        if sig.side.value == 'BUY' and htf_trend == 'down':
                            logger.warning(f"[MTF_SKIP] {sym}: 1H long blocked — {htf_timeframe} trend is DOWN")
                            continue
                        if sig.side.value == 'SELL' and htf_trend == 'up':
                            logger.warning(f"[MTF_SKIP] {sym}: 1H short blocked — {htf_timeframe} trend is UP")
                            continue
                    except Exception:
                        pass   # MTF check optional — don't block on error

                # -- Funding Rate filter (Bybit public API, perps only) ---------
                # Positive funding = longs overcrowded → avoid longs
                # Negative funding = shorts overcrowded → avoid shorts
                if funding_filter and sig is not None and ':' in sym:
                    try:
                        fr = market.fetch_funding_rate(sym)
                        if fr is not None:
                            if sig.side.value == 'BUY' and fr > funding_long_block:
                                logger.warning(f"[FR_SKIP] {sym}: long blocked — funding={fr:.4%} (longs overcrowded)")
                                continue
                            if sig.side.value == 'SELL' and fr < funding_short_block:
                                logger.warning(f"[FR_SKIP] {sym}: short blocked — funding={fr:.4%} (shorts overcrowded)")
                                continue
                            status['funding_rate'] = fr
                    except Exception:
                        pass   # Funding rate optional — don't block on error

                # FIX 5: Volume confirmation — current volume must exceed threshold
                curr_vol_ratio = df.iloc[-1].get('volume_ratio', 1.0) if not df.empty else 1.0
                vol_multiplier = CONFIG.get('volume_multiplier', 1.2)
                if curr_vol_ratio < vol_multiplier * 0.95:  # 5% tolerance for floating point
                    logger.warning(f"[SKIP] {sym}: SKIPPED — volume too low ({curr_vol_ratio:.1f}x < {vol_multiplier}x required)")
                    continue

                # Entry checklist gate
                passed, reason = _passes_entry_checklist(
                    macro_scale=status.get('risk_scale', 1.0),
                    sentiment_ok=sentiment_ok,
                    score=cand_score,
                    signal=sig,
                    circuit_breaker_ok=circuit_breaker_ok,
                    sniper_mode=SNIPER_MODE
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
                    ml_features, cand_score, min_score=MIN_SCORE
                )

                if not enter:
                    logger.warning(f"[ML_SKIP] {sym}: {ml_reason}")
                    continue

                dashboard_state['ai_confidence'] = confidence

                # Dump BTC risk factor (bypassed in sniper mode)
                if SNIPER_MODE:
                    btc_factor = 1.0
                else:
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

                # FIX 6: Risk-based position sizing — max loss = max_risk_per_trade_pct of balance
                # Use risk_to_qty for exchange-precision quantities
                max_risk_pct = CONFIG.get('max_risk_per_trade_pct', dynamic_risk)
                actual_risk_pct = min(dynamic_risk, max_risk_pct)
                market_struct = market.get_market_structure(sym)
                base_size = RiskEngine.risk_to_qty(
                    capital=current_bal - reserved,
                    risk_pct=actual_risk_pct,
                    entry_price=sig.price,
                    stop_price=sig.stop_loss,
                    market_structure=market_struct
                )
                if base_size <= 0:
                    # Fallback to old method if risk_to_qty returns 0
                    base_size = risk_engine.calculate_position_size(
                        sig, reserved_capital=reserved, dynamic_risk_pct=actual_risk_pct
                    )
                risk_scale = 1.0 if SNIPER_MODE else status.get('risk_scale', 1.0)
                size = base_size * risk_scale * btc_factor * (risk_mult if conservative else 1.0)

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
                    f"ATR={float(df.iloc[-1].get('atr', 0)):.6f} | regime={regime.value} | "
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
                        notifier.notify_entry(sym, sig, size, cand_score, arm_idx,
                                              atr=float(df.iloc[-1].get('atr', 0)),
                                              regime=regime.value)
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
