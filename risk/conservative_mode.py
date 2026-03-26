"""
Conservative mode for Swingbot.
Monitors performance and automatically reduces risk when danger is detected.
State is persisted in SQLite and survives restarts.
"""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Tuple

logger = logging.getLogger(__name__)


class ConservativeMode:
    """
    Watches recent trading performance and activates risk reduction when:
    - 3 consecutive losses
    - Daily loss exceeds 50% of the daily limit
    - Drawdown from peak exceeds 15%

    Deactivates after 2 consecutive wins while in conservative mode.
    State persists in SQLite via a dedicated table.
    """

    def __init__(self, store, config: dict = None):
        self.store = store
        self.config = config or {}
        self._ensure_table()
        self._state = self._load_state()

    def _ensure_table(self) -> None:
        """Create the conservative_mode_state table if not exists."""
        conn = self.store.get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conservative_mode_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active INTEGER DEFAULT 0,
                reason TEXT DEFAULT '',
                activated_at TEXT DEFAULT '',
                consecutive_wins INTEGER DEFAULT 0,
                risk_multiplier REAL DEFAULT 1.0
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO conservative_mode_state (id, active, reason, activated_at, consecutive_wins, risk_multiplier)
            VALUES (1, 0, '', '', 0, 1.0)
        """)
        conn.commit()
        conn.close()

    def _load_state(self) -> dict:
        """Load state from SQLite."""
        conn = self.store.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM conservative_mode_state WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'active': bool(row['active']),
                'reason': row['reason'] or '',
                'activated_at': row['activated_at'] or '',
                'consecutive_wins': row['consecutive_wins'] or 0,
                'risk_multiplier': row['risk_multiplier'] or 1.0,
            }
        return {'active': False, 'reason': '', 'activated_at': '',
                'consecutive_wins': 0, 'risk_multiplier': 1.0}

    def _save_state(self) -> None:
        """Persist state to SQLite."""
        conn = self.store.get_connection()
        conn.execute("""
            UPDATE conservative_mode_state SET
                active = ?, reason = ?, activated_at = ?,
                consecutive_wins = ?, risk_multiplier = ?
            WHERE id = 1
        """, (
            int(self._state['active']),
            self._state['reason'],
            self._state['activated_at'],
            self._state['consecutive_wins'],
            self._state['risk_multiplier'],
        ))
        conn.commit()
        conn.close()

    def _activate(self, reason: str, risk_mult: float) -> None:
        """Activate conservative mode."""
        self._state['active'] = True
        self._state['reason'] = reason
        self._state['activated_at'] = datetime.now(timezone.utc).isoformat()
        self._state['consecutive_wins'] = 0
        self._state['risk_multiplier'] = risk_mult
        self._save_state()
        logger.warning(f"[CONSERVATIVE] Activated: {reason} | risk x{risk_mult}")

    def _deactivate(self) -> None:
        """Deactivate conservative mode."""
        self._state['active'] = False
        self._state['reason'] = ''
        self._state['activated_at'] = ''
        self._state['consecutive_wins'] = 0
        self._state['risk_multiplier'] = 1.0
        self._save_state()
        logger.warning("[CONSERVATIVE] Deactivated — back to normal risk")

    def check(
        self,
        recent_trades: list,
        day_pnl: float,
        daily_limit: float,
        peak_balance: float,
        current_balance: float
    ) -> Tuple[bool, float, str]:
        """
        Check if conservative mode should be active.

        Args:
            recent_trades: List of recent trade dicts with 'pnl' key
            day_pnl: Today's P&L in dollars
            daily_limit: Daily loss limit percentage from config
            peak_balance: All-time peak balance
            current_balance: Current balance

        Returns:
            (conservative_active, risk_multiplier, reason)
        """
        cm_config = self.config.get('conservative_mode', {})
        if not cm_config.get('enabled', True):
            return False, 1.0, "OK"

        consecutive_losses_trigger = cm_config.get('consecutive_losses_trigger', 3)
        daily_loss_trigger_pct = cm_config.get('daily_loss_trigger_pct', 50)
        drawdown_trigger_pct = cm_config.get('drawdown_trigger_pct', 15)
        risk_reduction = cm_config.get('risk_reduction_pct', 50)
        wins_to_exit = cm_config.get('wins_to_exit', 2)

        risk_mult = 1.0 - (risk_reduction / 100.0)  # 50% reduction → 0.5

        # If already active, check for exit condition
        if self._state['active']:
            # Count consecutive wins from recent trades while in conservative
            if recent_trades:
                consec_wins = 0
                for t in recent_trades:
                    pnl = t.get('pnl', 0)
                    if isinstance(pnl, (int, float)) and pnl > 0:
                        consec_wins += 1
                    else:
                        break

                self._state['consecutive_wins'] = consec_wins
                self._save_state()

                if consec_wins >= wins_to_exit:
                    self._deactivate()
                    return False, 1.0, "OK"

            return True, risk_mult, self._state['reason']

        # Check trigger conditions

        # 1. Consecutive losses
        if recent_trades and len(recent_trades) >= consecutive_losses_trigger:
            consec_losses = 0
            for t in recent_trades:
                pnl = t.get('pnl', 0)
                if isinstance(pnl, (int, float)) and pnl < 0:
                    consec_losses += 1
                else:
                    break
            if consec_losses >= consecutive_losses_trigger:
                reason = f"{consec_losses} consecutive losses"
                self._activate(reason, risk_mult)
                return True, risk_mult, reason

        # 2. Daily loss exceeds threshold
        if current_balance > 0 and daily_limit > 0:
            daily_limit_dollars = current_balance * daily_limit / 100
            if day_pnl < 0 and abs(day_pnl) > daily_limit_dollars * (daily_loss_trigger_pct / 100):
                reason = f"Daily loss ${day_pnl:.2f} > {daily_loss_trigger_pct}% of limit"
                self._activate(reason, risk_mult)
                return True, risk_mult, reason

        # 3. Drawdown from peak
        if peak_balance > 0 and current_balance > 0:
            drawdown_pct = ((peak_balance - current_balance) / peak_balance) * 100
            if drawdown_pct > drawdown_trigger_pct:
                reason = f"Drawdown {drawdown_pct:.1f}% > {drawdown_trigger_pct}% threshold"
                self._activate(reason, risk_mult)
                return True, risk_mult, reason

        return False, 1.0, "OK"

    def get_status(self) -> dict:
        """
        Get current conservative mode status for the dashboard.
        """
        self._state = self._load_state()
        cm_config = self.config.get('conservative_mode', {})
        wins_to_exit = cm_config.get('wins_to_exit', 2)

        return {
            "active": self._state['active'],
            "reason": self._state['reason'] or "OK",
            "activated_at": self._state['activated_at'],
            "trades_in_conservative": self._state['consecutive_wins'],
            "wins_needed_to_exit": max(0, wins_to_exit - self._state['consecutive_wins']) if self._state['active'] else 0,
        }
