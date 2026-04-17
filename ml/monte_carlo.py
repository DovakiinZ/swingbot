"""
Monte Carlo simulation for trading strategy validation.

Tests if the strategy's profitability is robust or lucky.
Shuffles trade order N times and re-runs equity curve simulation.

Interpretation:
  - 95%+ profitable → strategy is genuinely edge-generating
  - 70-95% profitable → moderate edge, some luck involved
  - <70% profitable → likely overfitted, do NOT deploy live

Usage:
    python -m ml.monte_carlo --runs 1000 --days 90
"""
import argparse
import json
import logging
import math
import random
from pathlib import Path
from typing import List, Dict

import numpy as np

logger = logging.getLogger(__name__)


class MonteCarloSimulator:
    """Validates strategy edge via trade-order shuffling."""

    def __init__(self, trade_pnls: List[float], initial_balance: float = 1000.0):
        """
        Args:
            trade_pnls: List of % returns per trade (e.g. [2.5, -1.2, 3.1, -0.8])
            initial_balance: Starting balance for simulation
        """
        self.pnls = trade_pnls
        self.initial = initial_balance

    def simulate(self, n_runs: int = 1000, random_seed: int = 42) -> dict:
        """
        Run N simulations with shuffled trade orders.
        Returns stats on profitability distribution.
        """
        if len(self.pnls) < 5:
            return {'error': 'Need at least 5 trades for Monte Carlo'}

        random.seed(random_seed)
        np.random.seed(random_seed)

        final_balances = []
        max_drawdowns = []
        profitable_runs = 0

        for _ in range(n_runs):
            shuffled = self.pnls.copy()
            random.shuffle(shuffled)

            balance = self.initial
            peak = balance
            max_dd = 0.0

            for pnl_pct in shuffled:
                balance *= (1 + pnl_pct / 100)
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)

            final_balances.append(balance)
            max_drawdowns.append(max_dd * 100)
            if balance > self.initial:
                profitable_runs += 1

        final_arr = np.array(final_balances)
        dd_arr = np.array(max_drawdowns)

        profitable_pct = (profitable_runs / n_runs) * 100

        # Confidence interpretation
        if profitable_pct >= 95:
            verdict = "STRONG EDGE — deploy confidently"
        elif profitable_pct >= 80:
            verdict = "MODERATE EDGE — deploy with care"
        elif profitable_pct >= 60:
            verdict = "WEAK EDGE — more validation needed"
        else:
            verdict = "NO EDGE — likely overfit, do not deploy"

        return {
            'n_runs': n_runs,
            'n_trades': len(self.pnls),
            'initial_balance': self.initial,
            'profitable_runs_pct': round(profitable_pct, 1),
            'verdict': verdict,
            'balance_stats': {
                'mean': round(float(np.mean(final_arr)), 2),
                'median': round(float(np.median(final_arr)), 2),
                'std': round(float(np.std(final_arr)), 2),
                'min': round(float(np.min(final_arr)), 2),
                'max': round(float(np.max(final_arr)), 2),
                'p5': round(float(np.percentile(final_arr, 5)), 2),
                'p25': round(float(np.percentile(final_arr, 25)), 2),
                'p75': round(float(np.percentile(final_arr, 75)), 2),
                'p95': round(float(np.percentile(final_arr, 95)), 2),
            },
            'drawdown_stats': {
                'mean_pct': round(float(np.mean(dd_arr)), 2),
                'median_pct': round(float(np.median(dd_arr)), 2),
                'worst_pct': round(float(np.max(dd_arr)), 2),
                'p95_pct': round(float(np.percentile(dd_arr, 95)), 2),
            },
        }


def run_from_trades_db(db_path: str = "swingbot.db", n_runs: int = 1000) -> dict:
    """Run Monte Carlo using trades from the production database."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT pnl_percent FROM trades WHERE pnl_percent IS NOT NULL AND pnl_percent != 0"
    )
    rows = cursor.fetchall()
    conn.close()

    pnls = [row[0] for row in rows]
    if not pnls:
        return {'error': 'No closed trades in database'}

    sim = MonteCarloSimulator(pnls)
    return sim.simulate(n_runs=n_runs)


def run_from_backtest(backtest_json: str = "backtest_results.json",
                       n_runs: int = 1000) -> dict:
    """Run Monte Carlo on backtest results."""
    with open(backtest_json) as f:
        data = json.load(f)

    trades = data.get('trades', [])
    pnls = [t['pnl_pct'] for t in trades if t.get('pnl_pct') is not None]
    if not pnls:
        return {'error': 'No trades in backtest file'}

    sim = MonteCarloSimulator(pnls)
    return sim.simulate(n_runs=n_runs)


def main():
    parser = argparse.ArgumentParser(description='Monte Carlo Strategy Validator')
    parser.add_argument('--runs', type=int, default=1000)
    parser.add_argument('--db', type=str, default='swingbot.db')
    parser.add_argument('--backtest', type=str, default=None,
                        help='Use backtest_results.json instead of DB')
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format='%(message)s')

    print(f"\n{'='*60}")
    print(f"  MONTE CARLO SIMULATION — {args.runs} runs")
    print(f"{'='*60}")

    if args.backtest:
        result = run_from_backtest(args.backtest, n_runs=args.runs)
    else:
        result = run_from_trades_db(args.db, n_runs=args.runs)

    if 'error' in result:
        print(f"\n❌ {result['error']}")
        return

    print(f"\n  Total trades used:     {result['n_trades']}")
    print(f"  Profitable runs:       {result['profitable_runs_pct']}%")
    print(f"  Verdict:               {result['verdict']}")

    b = result['balance_stats']
    print(f"\n  Final balance (mean):  ${b['mean']}")
    print(f"  Final balance (median): ${b['median']}")
    print(f"  5th percentile:        ${b['p5']} (worst case)")
    print(f"  95th percentile:       ${b['p95']} (best case)")

    d = result['drawdown_stats']
    print(f"\n  Max drawdown (mean):   {d['mean_pct']}%")
    print(f"  Max drawdown (worst):  {d['worst_pct']}%")
    print(f"  Max drawdown (p95):    {d['p95_pct']}%")

    out = Path('montecarlo_results.json')
    with open(out, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\nResults saved to {out}")


if __name__ == '__main__':
    main()
