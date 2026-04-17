"""
Professional performance metrics for strategy evaluation.

Computes multiple risk-adjusted metrics beyond just win rate:
  - Sharpe Ratio   — total return per unit of volatility (annualized)
  - Sortino Ratio  — return per unit of DOWNSIDE volatility only
  - Calmar Ratio   — annualized return / max drawdown
  - Omega Ratio    — probability-weighted gains vs losses
  - Profit Factor  — total wins / total losses
  - Expectancy     — average $ gain per trade
  - Recovery Factor — net profit / max drawdown
  - Kelly Fraction — mathematically optimal bet size

Each metric answers a different question. A strategy good on ALL of them is real.
"""
import math
from typing import Dict, List

import numpy as np


def sharpe_ratio(returns: List[float], risk_free: float = 0.0,
                  periods_per_year: int = 252) -> float:
    """
    Sharpe = (mean_return - risk_free) / std_return × sqrt(periods_per_year)
    SR > 1.0 → decent, > 2.0 → excellent, > 3.0 → exceptional.
    """
    if len(returns) < 3:
        return 0.0
    log_r = [math.log(1 + r / 100) for r in returns if r != 0]
    if len(log_r) < 3:
        return 0.0
    mean_r = np.mean(log_r)
    std_r = np.std(log_r)
    if std_r == 0:
        return 0.0
    return float((mean_r - risk_free) / std_r * math.sqrt(periods_per_year))


def sortino_ratio(returns: List[float], risk_free: float = 0.0,
                   periods_per_year: int = 252) -> float:
    """
    Sortino = (mean_return - risk_free) / downside_std × sqrt(periods_per_year)
    Like Sharpe but only penalizes DOWNSIDE volatility. Upside vol is fine.
    Usually higher than Sharpe for positively-skewed strategies.
    """
    if len(returns) < 3:
        return 0.0
    log_r = [math.log(1 + r / 100) for r in returns if r != 0]
    if len(log_r) < 3:
        return 0.0
    mean_r = np.mean(log_r)
    downside = [r for r in log_r if r < 0]
    if not downside:
        return 10.0  # No losing trades — effectively infinite Sortino
    downside_std = np.std(downside)
    if downside_std == 0:
        return 0.0
    return float((mean_r - risk_free) / downside_std * math.sqrt(periods_per_year))


def calmar_ratio(annualized_return_pct: float, max_drawdown_pct: float) -> float:
    """
    Calmar = annual_return / max_drawdown
    > 1.0 → profitable with manageable drawdown, > 3.0 → excellent.
    """
    if max_drawdown_pct <= 0:
        return 0.0
    return round(annualized_return_pct / max_drawdown_pct, 3)


def omega_ratio(returns: List[float], threshold: float = 0.0) -> float:
    """
    Omega = sum(returns above threshold) / abs(sum(returns below threshold))
    > 1.0 → probability-weighted gains exceed losses at threshold.
    Captures fat-tail behavior Sharpe misses.
    """
    if not returns:
        return 0.0
    above = sum(r - threshold for r in returns if r > threshold)
    below = sum(threshold - r for r in returns if r < threshold)
    if below == 0:
        return 10.0
    return round(above / below, 3)


def profit_factor(pnls: List[float]) -> float:
    """Profit Factor = total gains / total losses. > 1.5 is commercial-grade."""
    if not pnls:
        return 0.0
    gains = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    if losses == 0:
        return 10.0
    return round(gains / losses, 3)


def expectancy(pnls: List[float]) -> float:
    """Average $ profit per trade. Must be > 0 for long-term profitability."""
    if not pnls:
        return 0.0
    return round(sum(pnls) / len(pnls), 4)


def recovery_factor(net_profit: float, max_drawdown: float) -> float:
    """Recovery Factor = net profit / max DD. > 2.0 means strategy recovers fast."""
    if max_drawdown <= 0:
        return 0.0
    return round(net_profit / max_drawdown, 3)


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Kelly = W - (1-W)/R where W = win rate (0-1), R = avg_win/avg_loss.
    Returns optimal fraction of capital to risk per trade.
    Capped at 0.25 (quarter-Kelly) for real-world safety.
    """
    if avg_loss <= 0 or win_rate <= 0:
        return 0.0
    r = avg_win / avg_loss
    kelly = win_rate - (1 - win_rate) / r
    return round(max(0.0, min(kelly, 0.25)), 4)


def max_drawdown_from_equity(equity_curve: List[float]) -> float:
    """Compute maximum drawdown % from equity curve."""
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        peak = max(peak, val)
        if peak > 0:
            dd = (peak - val) / peak
            max_dd = max(max_dd, dd)
    return round(max_dd * 100, 2)


def compute_all_metrics(trades: List[Dict], initial_balance: float = 1000.0) -> Dict:
    """
    Compute all performance metrics from a list of trade dicts.
    Each trade dict needs: pnl, pnl_pct.

    Returns a single dict with every metric — what serious traders look at.
    """
    if not trades:
        return {
            'total_trades': 0, 'win_rate': 0, 'sharpe': 0, 'sortino': 0,
            'calmar': 0, 'omega': 0, 'profit_factor': 0,
            'expectancy': 0, 'max_dd_pct': 0, 'kelly_fraction': 0,
            'total_return_pct': 0, 'recovery_factor': 0,
        }

    pnls = [t.get('pnl', 0) for t in trades]
    pnl_pcts = [t.get('pnl_pct', 0) for t in trades]

    # Basic stats
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win_pct = np.mean([p for p in pnl_pcts if p > 0]) if wins else 0
    avg_loss_pct = abs(np.mean([p for p in pnl_pcts if p < 0])) if losses else 0

    # Equity curve
    equity = [initial_balance]
    for p in pnls:
        equity.append(equity[-1] + p)
    final = equity[-1]
    net_profit = final - initial_balance
    total_return_pct = net_profit / initial_balance * 100
    max_dd_pct = max_drawdown_from_equity(equity)

    # Annualized return (assume trades span varies — use simple annualization)
    # If we have N trades and they took roughly T days, ann = (1 + total_return)^(365/T) - 1
    # Without dates, approximate based on trade count ≈ 1 per day (conservative)
    days_estimate = max(len(trades), 1)
    if total_return_pct > -100:
        try:
            ann_return = ((1 + total_return_pct / 100) ** (365 / days_estimate) - 1) * 100
        except (OverflowError, ValueError):
            ann_return = total_return_pct
    else:
        ann_return = -100

    return {
        'total_trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(win_rate, 2),
        'total_return_pct': round(total_return_pct, 2),
        'annualized_return_pct': round(ann_return, 2),
        'net_profit': round(net_profit, 2),
        'final_balance': round(final, 2),
        'max_dd_pct': max_dd_pct,
        'sharpe': round(sharpe_ratio(pnl_pcts), 3),
        'sortino': round(sortino_ratio(pnl_pcts), 3),
        'calmar': calmar_ratio(ann_return, max_dd_pct),
        'omega': omega_ratio(pnl_pcts),
        'profit_factor': profit_factor(pnls),
        'expectancy': expectancy(pnls),
        'recovery_factor': recovery_factor(net_profit, max_dd_pct),
        'kelly_fraction': kelly_fraction(win_rate / 100, avg_win_pct, avg_loss_pct),
        'avg_win_pct': round(float(avg_win_pct), 3),
        'avg_loss_pct': round(float(avg_loss_pct), 3),
        'best_trade': round(max(pnls), 2) if pnls else 0,
        'worst_trade': round(min(pnls), 2) if pnls else 0,
    }


def print_report(metrics: Dict) -> None:
    """Pretty-print a full metrics report."""
    print(f"\n{'='*50}")
    print("  PERFORMANCE REPORT")
    print(f"{'='*50}")
    print(f"  Total trades:      {metrics['total_trades']}")
    print(f"  Win rate:          {metrics['win_rate']}%  "
          f"({metrics['wins']}W / {metrics['losses']}L)")
    print(f"  Total return:      {metrics['total_return_pct']}%")
    print(f"  Annualized:        {metrics['annualized_return_pct']}%")
    print(f"  Final balance:     ${metrics['final_balance']}")
    print(f"  Max drawdown:      {metrics['max_dd_pct']}%")
    print()
    print("  RISK-ADJUSTED METRICS")
    print(f"  Sharpe ratio:      {metrics['sharpe']}  (>1=good, >2=excellent)")
    print(f"  Sortino ratio:     {metrics['sortino']}  (>1=good)")
    print(f"  Calmar ratio:      {metrics['calmar']}  (>1=good, >3=great)")
    print(f"  Omega ratio:       {metrics['omega']}  (>1.5=commercial)")
    print(f"  Profit factor:     {metrics['profit_factor']}  (>1.5=commercial)")
    print(f"  Expectancy:        ${metrics['expectancy']}/trade")
    print(f"  Recovery factor:   {metrics['recovery_factor']}  (>2=fast recovery)")
    print(f"  Kelly fraction:    {metrics['kelly_fraction']*100:.1f}% of capital")
    print(f"{'='*50}\n")
