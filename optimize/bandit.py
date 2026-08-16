import time
from typing import Dict, Optional
import numpy as np
from storage.sqlite_store import SQLiteStore
from optimize.param_sets import ARMS

REGIMES = ("trending_up", "trending_down", "ranging", "transition")
ABSTAIN_ARM = len(ARMS)

class Bandit:
    """Regime-specific Thompson Sampling with decay, exploration floor, and abstention."""
    def __init__(self, store: SQLiteStore, min_samples: int = 8, exploration_variance_floor: float = 0.08,
                 regime_switch_decay: float = 0.90):
        self.store = store
        self.min_samples = max(1, min_samples)
        self.exploration_variance_floor = max(0.0, exploration_variance_floor)
        self.regime_switch_decay = min(max(regime_switch_decay, 0.0), 1.0)
        self.n_arms = len(ARMS)
        self.states: Dict[str, Dict[str, list]] = {}
        self._reset_states()
        self._ensure_regime_column()

    def _reset_states(self):
        self.states = {r: {"counts": [0] * self.n_arms, "values": [0.0] * self.n_arms, "variances": [1.0] * self.n_arms} for r in REGIMES}

    @staticmethod
    def _normalize_regime(regime: Optional[str]) -> str:
        value = str(regime or "transition").lower()
        return {"range": "ranging", "choppy": "ranging", "trending_upward": "trending_up", "trending_downward": "trending_down"}.get(value, value if value in REGIMES else "transition")

    def _ensure_regime_column(self):
        conn = self.store.get_connection()
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(arm_performance)")}
            if "regime" not in columns:
                conn.execute("ALTER TABLE arm_performance ADD COLUMN regime TEXT NOT NULL DEFAULT 'transition'")
                conn.commit()
        finally:
            conn.close()

    def update_stats(self):
        self._reset_states()
        conn = self.store.get_connection()
        try:
            rows = conn.execute("SELECT arm_id, r_multiple, COALESCE(regime, 'transition') AS regime FROM arm_performance").fetchall()
        finally:
            conn.close()
        samples = {(r, a): [] for r in REGIMES for a in range(self.n_arms)}
        for row in rows:
            arm = int(row['arm_id'])
            if 0 <= arm < self.n_arms:
                samples[(self._normalize_regime(row['regime']), arm)].append(float(row['r_multiple']))
        for regime in REGIMES:
            for arm in range(self.n_arms):
                values = samples[(regime, arm)]
                if values:
                    state = self.states[regime]
                    state['counts'][arm] = len(values)
                    state['values'][arm] = float(np.mean(values))
                    state['variances'][arm] = max(self.exploration_variance_floor, 1.0 / (len(values) + 1.0))

    def select_arm_index(self, regime: Optional[str] = None) -> int:
        regime_name = self._normalize_regime(regime)
        if regime_name == 'ranging':
            return ABSTAIN_ARM
        self.update_stats()
        state = self.states[regime_name]
        draws = [np.random.normal(state['values'][i], np.sqrt(state['variances'][i])) for i in range(self.n_arms)]
        # Transition regimes intentionally retain exploration rather than locking onto stale arms.
        if regime_name == 'transition':
            draws = [draw * self.regime_switch_decay for draw in draws]
        return int(np.argmax(draws))

    @staticmethod
    def is_abstain(arm_id: int) -> bool:
        return arm_id == ABSTAIN_ARM

    def record_outcome(self, arm_id: int, r_multiple: float, pnl_pct: float, outcome: str, regime: Optional[str] = None):
        regime_name = self._normalize_regime(regime)
        conn = self.store.get_connection()
        try:
            conn.execute("INSERT INTO arm_performance (arm_id, timestamp, r_multiple, pnl_percent, outcome, regime) VALUES (?, ?, ?, ?, ?, ?)", (arm_id, int(time.time() * 1000), r_multiple, pnl_pct, outcome, regime_name))
            conn.commit()
        finally:
            conn.close()
