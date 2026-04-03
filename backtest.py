"""
Swingbot Backtester — Standalone trade simulation with full reporting.

Simulates the complete entry/exit logic against historical candle data:
  - Regime detection (TRENDING_UP / TRENDING_DOWN / RANGING)
  - Signal scoring (must pass threshold >= 70)
  - ATR-based SL/TP (1.5x ATR stop loss, 3.0x ATR take profit)
  - Walk-forward: one open position at a time per symbol

Usage:
    python backtest.py --symbol BTC/USDT --days 30
    python backtest.py --symbol ETH/USDT --days 90 --balance 500
"""
import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone
from typing import List, Dict, Optional

import yaml

from data.market import MarketData
from data.features import FeatureEngine
from strategy.regimes import RegimeDetector, MarketRegime
from strategy.signal_scorer import SignalScorer

logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger(__name__)


def run_backtest(
    symbol: str,
    days: int = 30,
    initial_balance: float = 1000.0,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
    score_threshold: int = 70,
    exchange_id: str = 'bybit',
) -> Dict:
    """
    Run a walk-forward backtest for a single symbol.

    Returns dict with trades list and performance metrics.
    """
    print(f"\n{'='*60}")
    print(f"  BACKTEST: {symbol} | {days} days | ${initial_balance:.0f} start")
    print(f"  SL: {sl_mult}x ATR | TP: {tp_mult}x ATR | Score >= {score_threshold}")
    print(f"{'='*60}\n")

    # Fetch historical data
    market = MarketData(exchange_id=exchange_id)
    timeframe = '1h'
    limit = days * 24  # 1 candle per hour

    print(f"Fetching {limit} candles for {symbol}...")
    candles = market.fetch_ohlcv(symbol, timeframe, limit=min(limit, 1000))
    if not candles or len(candles) < 50:
        print(f"ERROR: Not enough candles ({len(candles) if candles else 0}). Need at least 50.")
        return {"error": "insufficient_data"}

    df = FeatureEngine.compute_indicators(candles)
    if df.empty:
        print("ERROR: Feature computation failed.")
        return {"error": "feature_computation_failed"}

    print(f"Got {len(df)} candles with indicators. Starting simulation...\n")

    # Simulation state
    scorer = SignalScorer(threshold=score_threshold)
    balance = initial_balance
    peak_balance = initial_balance
    trades: List[Dict] = []
    position: Optional[Dict] = None  # {side, entry, sl, tp, size, entry_idx}

    # Walk forward candle by candle (skip first 50 for indicator warmup)
    for i in range(50, len(df)):
        row = df.iloc[i]
        close = row['close']
        high = row['high']
        low = row['low']
        atr = row.get('atr', 0)

        if atr <= 0 or close <= 0:
            continue

        # -- Check exits for open position --
        if position is not None:
            exited = False
            exit_price = 0
            exit_reason = ""

            if position['side'] == 'LONG':
                if low <= position['sl']:
                    exit_price = position['sl']
                    exit_reason = "SL"
                    exited = True
                elif high >= position['tp']:
                    exit_price = position['tp']
                    exit_reason = "TP"
                    exited = True
            else:  # SHORT
                if high >= position['sl']:
                    exit_price = position['sl']
                    exit_reason = "SL"
                    exited = True
                elif low <= position['tp']:
                    exit_price = position['tp']
                    exit_reason = "TP"
                    exited = True

            if exited:
                if position['side'] == 'LONG':
                    pnl = (exit_price - position['entry']) * position['size']
                else:
                    pnl = (position['entry'] - exit_price) * position['size']

                pnl_pct = (pnl / (position['entry'] * position['size'])) * 100
                balance += pnl
                peak_balance = max(peak_balance, balance)
                hold_bars = i - position['entry_idx']

                trade = {
                    'symbol': symbol,
                    'side': position['side'],
                    'entry_price': round(position['entry'], 6),
                    'exit_price': round(exit_price, 6),
                    'pnl': round(pnl, 4),
                    'pnl_pct': round(pnl_pct, 2),
                    'exit_reason': exit_reason,
                    'hold_hours': hold_bars,
                    'balance_after': round(balance, 2),
                }
                trades.append(trade)
                win_loss = "WIN" if pnl > 0 else "LOSS"
                print(f"  [{win_loss}] {position['side']} exit @ ${exit_price:.4f} | "
                      f"PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%) | {exit_reason} | "
                      f"Balance: ${balance:.2f}")
                position = None

        # -- Check new entry (only if no open position) --
        if position is None and i < len(df) - 1:
            regime = RegimeDetector.detect(row)

            # Regime gate: only trade in trending markets
            if regime == MarketRegime.RANGING:
                continue

            # Signal scorer gate
            # Pass a slice of df up to current candle for scoring
            df_slice = df.iloc[:i+1]
            scorer_result = scorer.score(df_slice, regime, symbol=symbol)
            if not scorer_result['passed']:
                continue

            # Determine direction from regime
            rsi = row.get('rsi', 50)
            ema_fast = row.get('ema_fast', 0)
            ema_slow = row.get('ema_slow', 0)

            if regime == MarketRegime.TRENDING_UP and rsi < 45 and ema_fast > ema_slow:
                side = 'LONG'
                sl = close - (atr * sl_mult)
                tp = close + (atr * tp_mult)
            elif regime == MarketRegime.TRENDING_DOWN and rsi > 55 and ema_fast < ema_slow:
                side = 'SHORT'
                sl = close + (atr * sl_mult)
                tp = close - (atr * tp_mult)
            else:
                continue

            # Position size: risk 2% of balance
            risk_pct = 0.02
            risk_amount = balance * risk_pct
            sl_dist = abs(close - sl)
            if sl_dist <= 0:
                continue
            size = risk_amount / sl_dist

            position = {
                'side': side,
                'entry': close,
                'sl': sl,
                'tp': tp,
                'size': size,
                'entry_idx': i,
            }
            print(f"  [ENTRY] {side} @ ${close:.4f} | "
                  f"SL: ${sl:.4f} | TP: ${tp:.4f} | ATR: ${atr:.6f} | "
                  f"Regime: {regime.value}")

    # Force-close any remaining position at last candle
    if position is not None:
        last_close = df.iloc[-1]['close']
        if position['side'] == 'LONG':
            pnl = (last_close - position['entry']) * position['size']
        else:
            pnl = (position['entry'] - last_close) * position['size']
        pnl_pct = (pnl / (position['entry'] * position['size'])) * 100
        balance += pnl
        trades.append({
            'symbol': symbol,
            'side': position['side'],
            'entry_price': round(position['entry'], 6),
            'exit_price': round(last_close, 6),
            'pnl': round(pnl, 4),
            'pnl_pct': round(pnl_pct, 2),
            'exit_reason': 'END',
            'hold_hours': len(df) - position['entry_idx'],
            'balance_after': round(balance, 2),
        })
        print(f"  [END] Force-closed {position['side']} @ ${last_close:.4f} | PnL: ${pnl:+.2f}")

    # Calculate metrics
    metrics = _calculate_metrics(trades, initial_balance, balance, peak_balance)
    _print_report(metrics, symbol)

    return {
        'symbol': symbol,
        'trades': trades,
        'metrics': metrics,
    }


def _calculate_metrics(
    trades: List[Dict],
    initial_balance: float,
    final_balance: float,
    peak_balance: float,
) -> Dict:
    """Calculate performance metrics from trade list."""
    total = len(trades)
    if total == 0:
        return {
            'total_trades': 0, 'win_rate': 0, 'avg_pnl': 0,
            'max_drawdown_pct': 0, 'sharpe_ratio': 0,
            'initial_balance': initial_balance, 'final_balance': final_balance,
            'total_return_pct': 0,
        }

    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    pnls = [t['pnl'] for t in trades]
    pnl_pcts = [t['pnl_pct'] for t in trades]

    # Max drawdown from equity curve
    equity = initial_balance
    peak = initial_balance
    max_dd = 0
    for t in trades:
        equity += t['pnl']
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Sharpe ratio (annualized, from trade returns)
    import numpy as np
    if len(pnl_pcts) >= 3:
        log_returns = [math.log(1 + p / 100) for p in pnl_pcts if p != 0]
        if log_returns:
            mean_r = np.mean(log_returns)
            std_r = np.std(log_returns)
            sharpe = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0
        else:
            sharpe = 0
    else:
        sharpe = 0

    total_return = ((final_balance - initial_balance) / initial_balance) * 100

    return {
        'total_trades': total,
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(len(wins) / total * 100, 1),
        'avg_pnl': round(sum(pnls) / total, 2),
        'avg_pnl_pct': round(sum(pnl_pcts) / total, 2),
        'best_trade': round(max(pnls), 2),
        'worst_trade': round(min(pnls), 2),
        'max_drawdown_pct': round(max_dd * 100, 2),
        'sharpe_ratio': round(sharpe, 2),
        'initial_balance': initial_balance,
        'final_balance': round(final_balance, 2),
        'total_return_pct': round(total_return, 2),
    }


def _print_report(metrics: Dict, symbol: str) -> None:
    """Print a formatted backtest report."""
    print(f"\n{'='*60}")
    print(f"  BACKTEST REPORT: {symbol}")
    print(f"{'='*60}")
    print(f"  Total Trades    : {metrics['total_trades']}")
    print(f"  Win Rate        : {metrics.get('win_rate', 0):.1f}%  "
          f"({metrics.get('wins', 0)}W / {metrics.get('losses', 0)}L)")
    print(f"  Avg P&L         : ${metrics.get('avg_pnl', 0):+.2f} "
          f"({metrics.get('avg_pnl_pct', 0):+.2f}%)")
    print(f"  Best Trade      : ${metrics.get('best_trade', 0):+.2f}")
    print(f"  Worst Trade     : ${metrics.get('worst_trade', 0):+.2f}")
    print(f"  Max Drawdown    : {metrics.get('max_drawdown_pct', 0):.2f}%")
    print(f"  Sharpe Ratio    : {metrics.get('sharpe_ratio', 0):.2f}")
    print(f"  ---")
    print(f"  Starting Balance: ${metrics['initial_balance']:.2f}")
    print(f"  Final Balance   : ${metrics.get('final_balance', 0):.2f}")
    print(f"  Total Return    : {metrics.get('total_return_pct', 0):+.2f}%")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description='Swingbot Backtester')
    parser.add_argument('--symbol', type=str, default='BTC/USDT',
                        help='Trading pair (default: BTC/USDT)')
    parser.add_argument('--days', type=int, default=30,
                        help='Days of history to backtest (default: 30)')
    parser.add_argument('--balance', type=float, default=1000.0,
                        help='Starting balance in USDT (default: 1000)')
    parser.add_argument('--exchange', type=str, default='bybit',
                        help='Exchange for data (default: bybit)')
    args = parser.parse_args()

    # Load config for ATR multipliers
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except Exception:
        config = {}

    sl_mult = config.get('atr_multiplier_sl', 1.5)
    tp_mult = config.get('atr_multiplier_tp', 3.0)
    score_threshold = config.get('signal_score_threshold', 70)

    result = run_backtest(
        symbol=args.symbol,
        days=args.days,
        initial_balance=args.balance,
        sl_mult=sl_mult,
        tp_mult=tp_mult,
        score_threshold=score_threshold,
        exchange_id=args.exchange,
    )

    # Save results to JSON
    output_file = 'backtest_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, default=str)
    print(f"Results saved to {output_file}")


if __name__ == '__main__':
    main()
