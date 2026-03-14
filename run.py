"""
Swingbot — main entry point
───────────────────────────
Scan cycle : every 2 hours
Strategy   : multi-symbol portfolio scan with scored setups
             RSI + EMA trend-following, confirmed by MACD / volume / BBands
"""
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
from strategy.scanner import MarketScanner, MIN_SCORE
from risk.risk_engine import RiskEngine
from risk.circuit_breakers import CircuitBreaker
from execution.broker_paper import PaperBroker
from execution.broker_binance import BinanceBroker
from optimize.bandit import Bandit
from reports.daily_report import DailyReport
from optimize.param_sets import ARMS
from data.sentiment import SentimentEngine
from strategy.selector import SymbolSelector
from data.polymarket_client import PolymarketClient
from strategy.macro_filter import compute_macro_risk_scale
from signals.dump_btc import get_btc_risk_factor_for_symbol

# ─── Config ───────────────────────────────────────────────────────────────────
load_dotenv()
with open("config.yaml", "r") as f:
    CONFIG = yaml.safe_load(f)

from core.i18n import i18n

# ─── Globals ──────────────────────────────────────────────────────────────────
store  = SQLiteStore(db_path=CONFIG['db_path'])
clock  = Clock(mode="live")
logger = logging.getLogger("swingbot")

SCAN_TOP_N    = CONFIG.get('scan_top_n', 20)
SCAN_INTERVAL = 2 * 60 * 60  # 2 hours

# Observability
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

    # ─── Three-gate live mode check ───────────────────────────────────────────
    check_live_env  = (os.getenv("TRADING_MODE", "paper").lower() == "live")
    check_live_file = os.path.exists("LIVE_OK.txt")
    check_live_conf = CONFIG.get("live", False)

    is_live = False
    if args.live:
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

    # ─── Logging ──────────────────────────────────────────────────────────────
    log_level_console = os.getenv("LOG_LEVEL_CONSOLE", "WARNING")
    log_level_file    = os.getenv("LOG_LEVEL_FILE", "DEBUG")
    setup_logging(console_level=log_level_console, file_level=log_level_file)

    # ─── Components ───────────────────────────────────────────────────────────
    market = MarketData(sandbox=False)

    if is_live:
        broker = BinanceBroker(store, market)
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

    poly_client = None
    if CONFIG.get('polymarket', {}).get('enabled', False):
        poly_client = PolymarketClient()

    context = {
        "symbol": args.symbol or CONFIG.get('symbol', 'BTC/USDT'),
        "timeframe": CONFIG['timeframe'],
        "lookback": CONFIG['lookback']
    }

    # ─── Startup Banner ───────────────────────────────────────────────────────
    mode_str = i18n.get("MODE_LIVE") if is_live else i18n.get("MODE_PAPER")
    timeframe = CONFIG['timeframe']
    lookback  = CONFIG['lookback']

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

    if scanner_enabled:
        print(f"Scanner: ENABLED | Max Positions: {max_positions} | Portfolio Risk Cap: {CONFIG.get('max_portfolio_risk_percent', 5.0)}%")
    else:
        print(f"Scanner: DISABLED | Single-symbol mode: {context['symbol']}")

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
        else:
            print(i18n.get("BANNER_PAPER_BAL").format(total=broker.get_balance()))
    print("="*50 + "\n")

    # ─── State ────────────────────────────────────────────────────────────────
    last_summary_date = datetime.utcnow().strftime('%Y-%m-%d')

    dashboard_state = {
        "positions_summary": [],
        "scan_results": [],
        "open_positions_count": 0,
        "last_cycle": None,
    }

    # ─── Main cycle ───────────────────────────────────────────────────────────
    def job():
        nonlocal last_summary_date
        cycle_start = time.time()

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

            # ── Balance / P&L sync ────────────────────────────────────────────
            current_bal = broker.get_balance()
            risk_engine.total_capital = current_bal

            today_str   = datetime.utcnow().strftime('%Y-%m-%d')
            daily_stats = store.get_daily_stats(today_str)
            day_pnl     = daily_stats.get('pnl', 0.0)
            status['pnl'] = day_pnl

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
                logger.warning(f"\n{header}\n{body}\n")
                reporter.generate_report(last_summary_date)
                last_summary_date = today_str

            # ── Circuit Breaker ───────────────────────────────────────────────
            if daily_stats.get('paused_until'):
                status['breaker'] = f"PAUSED({daily_stats['paused_until']})"
                logger.warning(f"[PAUSED] Trading halted until: {daily_stats['paused_until']}")
                return

            if day_pnl < -(current_bal * CONFIG['daily_loss_limit_percent'] / 100):
                logger.critical("Daily loss limit hit — halting for the day.")
                store.update_daily_stats(today_str, {'paused_until': 'Next Day'})
                return

            # ── Macro risk filter ─────────────────────────────────────────────
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

            # ── Sentiment gate ────────────────────────────────────────────────
            sentiment_ok = sentiment_engine.is_market_safe(
                threshold=CONFIG.get('sentiment_threshold', 20)
            )
            if not sentiment_ok:
                status['breaker'] = "SENTIMENT_FEAR"
                logger.warning("[SENTIMENT] Extreme fear detected — no new entries this cycle.")

            # ── Get all open positions ────────────────────────────────────────
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

            # ══════════════════════════════════════════════════════════════════
            # PHASE A — Process exits for every open position
            # ══════════════════════════════════════════════════════════════════
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
                    unrealized = (current_candle.close - pos.entry_price) * pos.amount
                    for ps in dashboard_state['positions_summary']:
                        if ps['symbol'] == sym:
                            ps['unrealized_pnl'] = round(unrealized, 4)

                    arm_idx = bandit.select_arm_index()
                    params  = ARMS[arm_idx]

                    # Technical exit signal
                    sig = strategy.check_signal(df, regime, params,
                                                current_position=True, symbol=sym)
                    if sig and sig.side == Side.SELL:
                        logger.warning(f"[EXIT] {sym} | reason={sig.reason.value} | price={sig.price:.4f}")
                        broker.place_order(sig, pos.amount)
                        status['pos_state'] = "CLOSING"
                        continue

                    # Paper SL/TP simulation
                    if not is_live:
                        exit_sig = broker.check_sl_tp(current_candle, symbol=sym)
                        if exit_sig:
                            logger.warning(f"[SL/TP] {sym} | reason={exit_sig.reason.value}")
                            broker.place_order(exit_sig, pos.amount)
                            status['pos_state'] = "CLOSING_SLTP"

                except Exception as e:
                    logger.error(f"Exit check error for {pos.symbol}: {e}", exc_info=True)

            # ══════════════════════════════════════════════════════════════════
            # PHASE B — Scan for new entry opportunities
            # ══════════════════════════════════════════════════════════════════
            open_positions = broker.get_open_positions()
            slots_available = risk_engine.max_open_positions - len(open_positions)

            if slots_available <= 0:
                logger.warning("[SCAN] All position slots filled — skipping entry scan.")
                _log_cycle_summary(current_bal, day_pnl, len(open_positions), time.time() - cycle_start)
                _log_status(status, time.time() - cycle_start)
                return

            if not sentiment_ok:
                _log_cycle_summary(current_bal, day_pnl, len(open_positions), time.time() - cycle_start)
                _log_status(status, time.time() - cycle_start)
                return

            # Get universe of symbols to scan
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
                    score  = scanner.score_symbol(df, regime)

                    if score >= MIN_SCORE:
                        scored.append({
                            'symbol':  sym,
                            'score':   score,
                            'df':      df,
                            'regime':  regime,
                            'candles': candles,
                            'price':   candles[-1].close,
                        })
                        logger.debug(f"  {sym}: score={score:.1f}")

                except Exception as e:
                    logger.error(f"Scan error for {sym}: {e}")

            scored.sort(key=lambda x: x['score'], reverse=True)

            # Save scan results to dashboard state
            if scored:
                dashboard_state['scan_results'] = [
                    {
                        "symbol": s['symbol'],
                        "score": s['score'],
                        "price": s['price'],
                    }
                    for s in scored
                ]
                logger.warning(
                    f"[SCAN] {len(scored)} qualified setup(s). "
                    f"Top: {scored[0]['symbol']} ({scored[0]['score']:.0f}/100)"
                )
            else:
                logger.warning("[SCAN] No high-quality setups found this cycle.")

            # ── Open positions for top candidates ─────────────────────────────
            entries_opened = 0
            for candidate in scored[:slots_available]:
                sym    = candidate['symbol']
                df     = candidate['df']
                regime = candidate['regime']

                arm_idx = bandit.select_arm_index()
                params  = ARMS[arm_idx]

                sig = strategy.check_signal(df, regime, params,
                                            current_position=False, symbol=sym)
                if not sig or sig.side != Side.BUY:
                    continue

                # Dump BTC risk factor
                btc_factor = get_btc_risk_factor_for_symbol(sym, status, CONFIG)
                if btc_factor <= 0:
                    logger.info(f"Dump BTC blocked entry for {sym}")
                    continue

                # Reserved capital
                reserved = sum(p.entry_price * p.amount for p in open_positions)

                # Risk sizing
                base_size = risk_engine.calculate_position_size(sig, reserved_capital=reserved)
                size      = base_size * status.get('risk_scale', 1.0) * btc_factor

                if size <= 0:
                    continue

                # Portfolio risk check
                ok, msg = risk_engine.can_open_position_for_symbol(
                    sym, open_positions, size, sig.price
                )
                if not ok:
                    logger.warning(f"[SKIP] {sym}: risk check failed — {msg}")
                    continue

                market_struct = market.get_market_structure(sym)
                ok, msg = risk_engine.check_min_notional(size, sig.price, market_struct)
                if not ok:
                    logger.warning(f"[SKIP] {sym}: min-notional check failed — {msg}")
                    continue

                logger.warning(
                    f"[BUY] {sym} | score={candidate['score']:.0f} | "
                    f"price={sig.price:.4f} | size={size:.6f} | "
                    f"SL={sig.stop_loss:.4f} | TP={sig.take_profit:.4f}"
                )
                order = broker.place_order(sig, size)
                if order:
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
            _log_status(status, time.time() - cycle_start)

            if scanner_enabled and open_positions:
                logger.warning(f"  Open Positions ({len(open_positions)}/{max_positions}): " +
                             ", ".join(f"{p.symbol}@{p.entry_price:.2f}" for p in open_positions))

        except Exception as e:
            logger.error(f"Cycle Error: {e}", exc_info=True)
            circuit_breaker.record_api_error()

    # ─── Helpers ──────────────────────────────────────────────────────────────
    def _log_cycle_summary(balance, pnl, open_pos, elapsed, entries_opened=0):
        next_scan = max(0, int(SCAN_INTERVAL - elapsed))
        m, s = divmod(next_scan, 60)
        logger.warning(
            f"[DONE] Balance: ${balance:.2f} | Open: {open_pos} pos | "
            f"Day PnL: {pnl:+.2f} | Entries: {entries_opened} | "
            f"Next scan in: {m}m {s}s"
        )

    def _log_status(status, elapsed):
        next_wait = max(0, int(SCAN_INTERVAL - elapsed))
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

    # ─── Run ──────────────────────────────────────────────────────────────────
    if args.once:
        job()
    else:
        logger.warning("Starting Swingbot — 2-hour scan cycle. Press Ctrl+C to stop.")
        job()  # Run immediately on start
        schedule.every(2).hours.do(job)
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
