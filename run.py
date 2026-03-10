"""
Swingbot — main entry point
───────────────────────────
Scan cycle: every 2 hours
Strategy  : multi-symbol portfolio scan with scored setups
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

# ─── Config ───────────────────────────────────────────────────────────────────
load_dotenv()
with open("config.yaml", "r") as f:
    CONFIG = yaml.safe_load(f)

from core.i18n import i18n

# ─── Globals ──────────────────────────────────────────────────────────────────
store  = SQLiteStore(db_path=CONFIG['db_path'])
clock  = Clock(mode="live")
logger = logging.getLogger("swingbot")

# How many symbols to scan every cycle
SCAN_TOP_N   = CONFIG.get('scan_top_n', 20)
# Seconds between scans (2 hours)
SCAN_INTERVAL = 2 * 60 * 60


def main():
    parser = argparse.ArgumentParser(description="Swingbot")
    parser.add_argument("--paper", action="store_true", help="Run in paper mode (default)")
    parser.add_argument("--live",  action="store_true", help="Run in LIVE mode (DANGEROUS)")
    parser.add_argument("--once",  action="store_true", help="Run one cycle and exit")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Force a single trading pair (skips auto-scan)")
    parser.add_argument("--lang",   type=str, help="Language (en, ar)")
    parser.add_argument("--guide",  action="store_true",
                        help="Show bilingual help menu / عرض القائمة")
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

    strategy        = RsiEmaStrategy()
    scanner         = MarketScanner()
    bandit          = Bandit(store, exploration_prob=CONFIG['bandit']['exploration_prob'])
    reporter        = DailyReport(store)
    sentiment_engine = SentimentEngine()
    selector        = SymbolSelector(market.exchange)

    poly_client = None
    if CONFIG.get('polymarket', {}).get('enabled', False):
        poly_client = PolymarketClient()

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
    print(f"Scan cycle: every 2 hours | Max positions: {CONFIG['max_open_positions']}")

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

    # ─── Main cycle ───────────────────────────────────────────────────────────
    def job():
        nonlocal last_summary_date
        cycle_start = time.time()

        try:
            logger.debug(f"=== 2-Hour Cycle Start: {datetime.utcnow()} ===")

            # ── Balance / P&L sync ────────────────────────────────────────────
            current_bal = broker.get_balance()
            risk_engine.total_capital = current_bal

            today_str  = datetime.utcnow().strftime('%Y-%m-%d')
            daily_stats = store.get_daily_stats(today_str)
            day_pnl    = daily_stats.get('pnl', 0.0)

            # Daily report rollover
            if last_summary_date != today_str:
                stats = store.get_daily_trade_stats(last_summary_date)
                header = i18n.get("SUMMARY_HEADER").format(date=last_summary_date)
                body = i18n.get("SUMMARY_STATS").format(
                    count=stats['count'] if 'count' in stats else 0,
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
                logger.warning(f"[PAUSED] Trading halted until: {daily_stats['paused_until']}")
                return

            if day_pnl < -(current_bal * CONFIG['daily_loss_limit_percent'] / 100):
                logger.critical("Daily loss limit hit — halting for the day.")
                store.update_daily_stats(today_str, {'paused_until': 'Next Day'})
                return

            # ── Macro risk filter ─────────────────────────────────────────────
            risk_scale = 1.0
            macro_prob = 0.0

            if poly_client:
                pm_conf = CONFIG['polymarket']
                update_interval = pm_conf.get('update_hours', 6) * 3600
                last_snap = store.get_latest_polymarket_snapshot()

                need_update = True
                if last_snap and (time.time() - last_snap['timestamp']) < update_interval:
                    need_update = False
                    macro_prob = last_snap['probability']
                    risk_scale = last_snap['risk_scale']

                if need_update:
                    probs = [p for m in pm_conf.get('markets', [])
                             for p in [poly_client.get_probability(m)] if p is not None]
                    if probs:
                        risk_scale = compute_macro_risk_scale(probs)
                        macro_prob = sum(probs) / len(probs)
                        store.save_polymarket_snapshot(int(time.time()), "multi", macro_prob, risk_scale)
                    elif last_snap:
                        risk_scale = last_snap['risk_scale']
                        macro_prob = last_snap['probability']
                    else:
                        risk_scale = pm_conf.get('default_risk_scale_on_failure', 0.7)

            # ── Sentiment gate ────────────────────────────────────────────────
            sentiment_ok = sentiment_engine.is_market_safe(
                threshold=CONFIG.get('sentiment_threshold', 20)
            )
            if not sentiment_ok:
                logger.warning("[SENTIMENT] Extreme fear detected — no new entries this cycle.")

            # ── Get all open positions ─────────────────────────────────────────
            open_positions = {p.symbol: p for p in broker.get_open_positions()}

            logger.warning(
                f"[CYCLE] {datetime.utcnow().strftime('%H:%M:%S')} UTC | "
                f"Balance: ${current_bal:.2f} | Open positions: {len(open_positions)} | "
                f"Day PnL: {day_pnl:+.2f} | Macro scale: {risk_scale:.2f}"
            )

            # ══════════════════════════════════════════════════════════════════
            # PHASE 1 — Process exits for every open position
            # ══════════════════════════════════════════════════════════════════
            for sym, pos in list(open_positions.items()):
                try:
                    candles = market.fetch_ohlcv(sym, timeframe, limit=lookback)
                    if not candles:
                        continue

                    df     = FeatureEngine.compute_indicators(candles)
                    if df.empty:
                        continue

                    regime = RegimeDetector.detect(df.iloc[-1])
                    arm_idx = bandit.select_arm_index()
                    params  = ARMS[arm_idx]

                    # Technical exit signal
                    sig = strategy.check_signal(df, regime, params,
                                                current_position=True, symbol=sym)
                    if sig and sig.side == Side.SELL:
                        logger.warning(f"[EXIT] {sym} | reason={sig.reason.value} | price={sig.price:.4f}")
                        broker.place_order(sig, pos.amount)
                        continue

                    # Paper SL/TP simulation
                    if not is_live:
                        exit_sig = broker.check_sl_tp(candles[-1], symbol=sym)
                        if exit_sig:
                            logger.warning(f"[SL/TP] {sym} | reason={exit_sig.reason.value}")
                            broker.place_order(exit_sig, pos.amount)

                except Exception as e:
                    logger.error(f"Exit check error for {sym}: {e}", exc_info=True)

            # ══════════════════════════════════════════════════════════════════
            # PHASE 2 — Scan for new entry opportunities
            # ══════════════════════════════════════════════════════════════════
            # Refresh position count after exits
            current_open_count = len(broker.get_open_positions())
            slots_available    = risk_engine.max_open_positions - current_open_count

            if slots_available <= 0:
                logger.warning("[SCAN] All position slots filled — skipping entry scan.")
                _log_cycle_summary(current_bal, day_pnl, current_open_count, time.time() - cycle_start)
                return

            if not sentiment_ok:
                _log_cycle_summary(current_bal, day_pnl, current_open_count, time.time() - cycle_start)
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
            already_held = set(broker.get_open_positions()[i].symbol
                               for i in range(len(broker.get_open_positions())))

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

            # Sort best first
            scored.sort(key=lambda x: x['score'], reverse=True)

            if scored:
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

                # Risk sizing
                base_size = risk_engine.calculate_position_size(sig)
                size      = base_size * risk_scale

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
                broker.place_order(sig, size)
                entries_opened += 1

            _log_cycle_summary(broker.get_balance(), day_pnl,
                               len(broker.get_open_positions()),
                               time.time() - cycle_start,
                               entries_opened=entries_opened)

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
