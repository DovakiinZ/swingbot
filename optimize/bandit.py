import time
from typing import Dict, Optional

import numpy as np

from storage.sqlite_store import SQLiteStore
from optimize.param_sets import ARMS


REGIMES = ("trending_up", "trending_down", "ranging", "transition")
ABSTAIN_ARM = len(ARMS)


class Bandit:
    """Regime-specific Gaussian Thompson Sampling with a no-trade arm.

    The public methods remain backward compatible: callers that do not provide
    a regime use ``transition``. Ranging is deliberately abstention-first.
    """

    def __init__(self, store: SQLiteStore, min_samples: int = 5):
        self.store = store
        self.min_samples = min_samples
        self.n_arms = len(ARMS)
        self.states: Dict[str, Dict[str, list]] = {}
        self._reset_states()
        self._ensure_regime_column()

    def _reset_states(self) -> None:
        self.states = {
            regime: {
                "counts": [0] * self.n_arms,
                "values": [0.0] * self.n_arms,
                "variances": [1.0] * self.n_arms,
            }
            for regime in REGIMES
        }

    @staticmethod
    def _normalize_regime(regime: Optional[str]) -> str:
        value = str(regime or "transition").lower()
        aliases = {
            "trending_up": "trending_up", "trending_upward": "trending_up",
            "trending_down": "trending_down", "trending_downward": "trending_down",
            "ranging": "ranging", "range": "ranging", "choppy": "ranging",
            "transition": "transition",
        }
        return aliases.get(value, "transition")

    def _ensure_regime_column(self) -> None:
        """Upgrade existing databases without requiring a manual migration."""
        conn = self.store.get_connection()
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(arm_performance)")}
            if "regime" not in columns:
                conn.execute("ALTER TABLE arm_performance ADD COLUMN regime TEXT NOT NULL DEFAULT 'transition'")
                conn.commit()
        finally:
            conn.close()

    def update_stats(self) -> None:
        self._reset_states()
        conn = self.store.get_connection()
        try:
            rows = conn.execute(
                "SELECT arm_id, r_multiple, COALESCE(regime, 'transition') AS regime "
                "FROM arm_performance"
            ).fetchall()
        finally:
            conn.close()

        rewards = {(regime, arm): [] for regime in REGIMES for arm in range(self.n_arms)}
        for row in rows:
            arm_id = int(row["arm_id"])
            if 0 <= arm_id < self.n_arms:
                regime = self._normalize_regime(row["regime"])
                rewards[(regime, arm_id)].append(float(row["r_multiple"]))

        for regime in REGIMES:
            state = self.states[regime]
            for arm_id in range(self.n_arms):
                samples = rewards[(regime, arm_id)]
                state["counts"][arm_id] = len(samples)
                if samples:
                    state["values"][arm_id] = float(np.mean(samples))
                    state["variances"][arm_id] = 1.0 / (len(samples) + 1.0)

    def select_arm_index(self, regime: Optional[str] = None) -> int:
        regime_name = self._normalize_regime(regime)
        if regime_name == "ranging":
            return ABSTAIN_ARM
        self.update_stats()
        state = self.states[regime_name]
        samples = [
            np.random.normal(state["values"][i], np.sqrt(state["variances"][i]))
            for i in range(self.n_arms)
        ]
        return int(np.argmax(samples))

    @staticmethod
    def is_abstain(arm_id: int) -> bool:
        return arm_id == ABSTAIN_ARM

    def record_outcome(
        self,
        arm_id: int,
        r_multiple: float,
        pnl_pct: float,
        outcome: str,
        regime: Optional[str] = None,
    ) -> None:
        regime_name = self._normalize_regime(regime)
        conn = self.store.get_connection()
        try:
            conn.execute(
                "INSERT INTO arm_performance "
                "(arm_id, timestamp, r_multiple, pnl_percent, outcome, regime) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (arm_id, int(time.time() * 1000), r_multiple, pnl_pct, outcome, regime_name),
            )
            conn.commit()
        finally:
            conn.close()
