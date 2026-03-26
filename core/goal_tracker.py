"""
Goal Tracker — tracks progress from starting balance to $1000 target.
Calculates phase, overall progress, and projected completion.
"""
from typing import Dict, Any


class GoalTracker:
    """
    Tracks progress from starting balance to $1000 target.
    Calculates phase, overall progress, and projected completion.
    """

    PHASES = [
        {"phase": 1, "start": 0,   "target": 250,  "risk_pct": 3.0},
        {"phase": 2, "start": 250, "target": 500,  "risk_pct": 3.5},
        {"phase": 3, "start": 500, "target": 1000, "risk_pct": 4.0},
    ]

    def __init__(self, config: dict, store):
        self.start_balance = config.get('base_balance', 100.0)
        self.target_balance = config.get('goal_balance', 1000.0)
        self.store = store

    def get_status(self, current_balance: float) -> Dict[str, Any]:
        """
        Returns full goal tracker status for dashboard.

        Overall progress:
          progress_pct = (current - start) / (target - start) * 100

        Current phase:
          Which PHASE bracket does current_balance fall in?

        Phase progress:
          How far through THIS phase are we?

        Projection:
          Based on avg P&L per trade, how many more trades needed?
        """
        denominator = self.target_balance - self.start_balance
        if denominator <= 0:
            overall_progress = 100.0
        else:
            overall_progress = min(
                (current_balance - self.start_balance) / denominator * 100,
                100.0
            )
            overall_progress = max(overall_progress, 0.0)

        # Current phase
        current_phase = self.PHASES[0]
        for phase in self.PHASES:
            if current_balance >= phase['start']:
                current_phase = phase

        phase_range = current_phase['target'] - current_phase['start']
        if phase_range > 0:
            phase_progress = min(
                (current_balance - current_phase['start']) / phase_range * 100,
                100.0
            )
            phase_progress = max(phase_progress, 0.0)
        else:
            phase_progress = 100.0

        # Projection
        stats = self.store.get_overall_stats()
        total_trades = stats.get('total_trades', 0)
        avg_pnl_per_trade = stats.get('avg_pnl', 0)

        if avg_pnl_per_trade > 0:
            remaining = self.target_balance - current_balance
            if remaining <= 0:
                projection = "Goal reached!"
            else:
                est_trades = int(remaining / avg_pnl_per_trade)
                projection = f"~{est_trades} more trades at current pace"
        else:
            projection = "Collecting data..."

        return {
            'start_balance':        self.start_balance,
            'current_balance':      round(current_balance, 2),
            'target_balance':       self.target_balance,
            'progress_pct':         round(overall_progress, 2),
            'phase':                current_phase['phase'],
            'phase_name':           f"Phase {current_phase['phase']}",
            'phase_start':          current_phase['start'],
            'phase_target':         current_phase['target'],
            'phase_progress_pct':   round(phase_progress, 2),
            'trades_completed':     total_trades,
            'trades_target':        100,
            'on_track':             avg_pnl_per_trade > 0,
            'projected_completion': projection,
        }
