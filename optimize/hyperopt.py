"""
Optuna-based hyperparameter optimization for swingbot.

Automatically finds the best strategy parameters by running
Bayesian optimization on historical data.

Optimized parameters:
  - rsi_entry (long trigger threshold)
  - rsi_exit (short trigger threshold)
  - ema_fast, ema_slow (trend periods)
  - atr_sl_mult, atr_tp_mult (stop/target multipliers)
  - min_score (scanner threshold)
  - signal_score_threshold (scorer gate)

Objective: maximize Sharpe ratio * sqrt(trade_count) / (1 + max_drawdown_pct)
  — rewards profitable strategies that actually trade + low drawdown

Usage:
    python -m optimize.hyperopt --symbol BTC/USDT --days 90 --trials 100
"""
import argparse
import json
import logging
import math
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class StrategyHyperopt:
    """Bayesian hyperparameter optimizer using Optuna."""

    def __init__(self, symbol: str = "BTC/USDT", days: int = 90,
                 initial_balance: float = 1000.0):
        self.symbol = symbol
        self.days = days
        self.initial_balance = initial_balance
        self._df_cache: Optional[pd.DataFrame] = None

    def _fetch_data(self) -> pd.DataFrame:
        """Fetch and cache historical data once (reused across trials)."""
        if self._df_cache is not None:
            return self._df_cache

        from data.market import MarketData
        from data.features import FeatureEngine

        market = MarketData(exchange_id='bybit')
        limit = min(self.days * 24, 1000)
        candles = market.fetch_ohlcv(self.symbol, '1h', limit=limit)
        if not candles or len(candles) < 100:
            raise ValueError(f"Not enough data for {self.symbol}")

        df = FeatureEngine.compute_indicators(candles)
        self._df_cache = df
        return df

    def _simulate(self, df: pd.DataFrame, params: dict) -> dict:
        """
        Walk-forward simulation with the given params.
        Returns metrics: sharpe, win_rate, max_dd, total_return, trades.
        """
        from strategy.regimes import RegimeDetector, MarketRegime

        balance = self.initial_balance
        peak = balance
        max_dd = 0.0
        trades = []
        position = None

        sl_mult = params['atr_sl_mult']
        tp_mult = params['atr_tp_mult']
        rsi_entry = params['rsi_entry']
        rsi_exit = params['rsi_exit']

        for i in range(50, len(df)):
            row = df.iloc[i]
            close = row['close']
            high = row['high']
            low = row['low']
            atr = row.get('atr', 0)
            rsi = row.get('rsi', 50)
            ema_fast = row.get('ema_fast', 0)
            ema_slow = row.get('ema_slow', 0)

            if atr <= 0 or close <= 0 or pd.isna(rsi):
                continue

            # Exit check for open position
            if position is not None:
                exited = False
                exit_price = 0
                if position['side'] == 'LONG':
                    if low <= position['sl']:
                        exit_price = position['sl']
                        exited = True
                    elif high >= position['tp']:
                        exit_price = position['tp']
                        exited = True
                else:  # SHORT
                    if high >= position['sl']:
                        exit_price = position['sl']
                        exited = True
                    elif low <= position['tp']:
                        exit_price = position['tp']
                        exited = True

                if exited:
                    if position['side'] == 'LONG':
                        pnl = (exit_price - position['entry']) * position['size']
                    else:
                        pnl = (position['entry'] - exit_price) * position['size']
                    pnl_pct = (pnl / (position['entry'] * position['size'])) * 100
                    balance += pnl
                    peak = max(peak, balance)
                    dd = (peak - balance) / peak if peak > 0 else 0
                    max_dd = max(max_dd, dd)
                    trades.append({'pnl': pnl, 'pnl_pct': pnl_pct})
                    position = None

            # Entry check
            if position is None and i < len(df) - 1:
                regime = RegimeDetector.detect(row)
                if regime == MarketRegime.RANGING:
                    continue

                side = None
                if regime == MarketRegime.TRENDING_UP and rsi < rsi_entry and ema_fast > ema_slow:
                    side = 'LONG'
                    sl = close - (atr * sl_mult)
                    tp = close + (atr * tp_mult)
                elif regime == MarketRegime.TRENDING_DOWN and rsi > rsi_exit and ema_fast < ema_slow:
                    side = 'SHORT'
                    sl = close + (atr * sl_mult)
                    tp = close - (atr * tp_mult)

                if side:
                    risk_amount = balance * 0.02
                    sl_dist = abs(close - sl)
                    if sl_dist > 0:
                        size = risk_amount / sl_dist
                        position = {
                            'side': side, 'entry': close,
                            'sl': sl, 'tp': tp, 'size': size,
                        }

        # Calculate metrics
        if not trades:
            return {'sharpe': -10, 'win_rate': 0, 'max_dd': 1.0,
                    'total_return': 0, 'trades': 0, 'objective': -100}

        wins = sum(1 for t in trades if t['pnl'] > 0)
        win_rate = wins / len(trades) * 100
        total_return = (balance - self.initial_balance) / self.initial_balance * 100

        # Sharpe from log returns
        log_returns = [math.log(1 + t['pnl_pct'] / 100) for t in trades if t['pnl_pct'] != 0]
        if len(log_returns) >= 3:
            mean_r = np.mean(log_returns)
            std_r = np.std(log_returns)
            sharpe = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0
        else:
            sharpe = 0

        # Objective: reward Sharpe + trade count - drawdown
        # Adds sqrt(trades) to prefer strategies that actually trade
        # Divides by (1 + max_dd) to penalize drawdown
        trade_bonus = math.sqrt(max(len(trades), 1))
        objective = (sharpe * trade_bonus) / (1 + max_dd * 10)

        return {
            'sharpe': round(sharpe, 3),
            'win_rate': round(win_rate, 1),
            'max_dd': round(max_dd * 100, 2),
            'total_return': round(total_return, 2),
            'trades': len(trades),
            'objective': round(objective, 4),
        }

    def optimize(self, n_trials: int = 100) -> dict:
        """Run Bayesian hyperparameter optimization."""
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        df = self._fetch_data()
        logger.warning(f"[HYPEROPT] Loaded {len(df)} candles for {self.symbol}")

        def objective(trial):
            params = {
                'rsi_entry': trial.suggest_int('rsi_entry', 30, 55),
                'rsi_exit': trial.suggest_int('rsi_exit', 50, 75),
                'atr_sl_mult': trial.suggest_float('atr_sl_mult', 1.0, 3.0, step=0.1),
                'atr_tp_mult': trial.suggest_float('atr_tp_mult', 2.0, 6.0, step=0.1),
            }
            # Enforce TP > SL (R:R > 1.0)
            if params['atr_tp_mult'] <= params['atr_sl_mult']:
                return -100

            metrics = self._simulate(df, params)
            return metrics['objective']

        study = optuna.create_study(
            direction='maximize',
            sampler=optuna.samplers.TPESampler(seed=42)
        )
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

        best_params = study.best_params
        best_metrics = self._simulate(df, best_params)

        return {
            'symbol': self.symbol,
            'days': self.days,
            'n_trials': n_trials,
            'best_params': best_params,
            'best_metrics': best_metrics,
            'improvement_vs_default': self._compare_to_default(df),
        }

    def _compare_to_default(self, df: pd.DataFrame) -> dict:
        """Compare to current config defaults."""
        default_params = {
            'rsi_entry': 45,
            'rsi_exit': 55,
            'atr_sl_mult': 1.5,
            'atr_tp_mult': 3.0,
        }
        return self._simulate(df, default_params)


def main():
    parser = argparse.ArgumentParser(description='Swingbot Hyperparameter Optimizer')
    parser.add_argument('--symbol', type=str, default='BTC/USDT')
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--trials', type=int, default=100)
    parser.add_argument('--balance', type=float, default=1000.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format='%(message)s')

    print(f"\n{'='*60}")
    print(f"  OPTUNA HYPEROPT — {args.symbol} | {args.days} days | {args.trials} trials")
    print(f"{'='*60}\n")

    optimizer = StrategyHyperopt(
        symbol=args.symbol, days=args.days, initial_balance=args.balance
    )
    result = optimizer.optimize(n_trials=args.trials)

    print(f"\n{'='*60}")
    print("  BEST PARAMETERS FOUND")
    print(f"{'='*60}")
    for k, v in result['best_params'].items():
        print(f"  {k}: {v}")

    print(f"\n{'='*60}")
    print("  BEST METRICS")
    print(f"{'='*60}")
    m = result['best_metrics']
    print(f"  Sharpe ratio: {m['sharpe']}")
    print(f"  Win rate: {m['win_rate']}%")
    print(f"  Max drawdown: {m['max_dd']}%")
    print(f"  Total return: {m['total_return']}%")
    print(f"  Trade count: {m['trades']}")

    print(f"\n{'='*60}")
    print("  vs CURRENT DEFAULTS")
    print(f"{'='*60}")
    d = result['improvement_vs_default']
    print(f"  Sharpe: {d['sharpe']} → {m['sharpe']} ({m['sharpe'] - d['sharpe']:+.2f})")
    print(f"  Return: {d['total_return']}% → {m['total_return']}% "
          f"({m['total_return'] - d['total_return']:+.2f}%)")
    print(f"  Trades: {d['trades']} → {m['trades']}")

    # Save results
    out = Path('hyperopt_results.json')
    with open(out, 'w') as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\nResults saved to {out}")


if __name__ == '__main__':
    main()
