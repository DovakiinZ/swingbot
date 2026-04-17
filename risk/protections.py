"""
Advanced protections — fast-reaction risk guards beyond circuit breakers.

Inspired by Freqtrade's ProtectionManager. Each protection is a rule
that temporarily blocks trading when a specific bad pattern emerges.

Protections:
  1. StoplossGuard — pause if N stop-losses hit within X minutes
  2. CooldownPeriod — pause per-symbol after a loss
  3. MaxDrawdownProtection — pause if drawdown exceeds X% over N trades
  4. LowProfitPairs — disable symbols with negative expectancy
"""
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ProtectionStatus:
    """Result of a protection check."""
    blocked: bool
    symbol: Optional[str] = None
    until_ts: float = 0
    reason: str = ""


class StoplossGuard:
    """
    Pause all trading if too many stop-losses hit in a short window.

    Example: if 3 stop-losses trigger within 30 minutes → pause 4 hours.
    This stops the bot from bleeding out in a hostile market.
    """

    def __init__(self, lookback_minutes: int = 30,
                 trade_limit: int = 3, pause_hours: int = 4):
        self.lookback_sec = lookback_minutes * 60
        self.trade_limit = trade_limit
        self.pause_sec = pause_hours * 3600
        self._stoploss_times: deque = deque(maxlen=20)
        self._paused_until: float = 0

    def record_stoploss(self) -> None:
        """Call when a stop-loss hits."""
        self._stoploss_times.append(time.time())
        self._check_trigger()

    def _check_trigger(self) -> None:
        now = time.time()
        cutoff = now - self.lookback_sec
        recent = [t for t in self._stoploss_times if t >= cutoff]
        if len(recent) >= self.trade_limit:
            self._paused_until = now + self.pause_sec
            logger.warning(
                f"[PROTECTION] StoplossGuard triggered — "
                f"{len(recent)} stop-losses in {self.lookback_sec/60:.0f}min. "
                f"Trading paused {self.pause_sec/3600:.0f}h"
            )

    def check(self) -> ProtectionStatus:
        now = time.time()
        if now < self._paused_until:
            remaining = int(self._paused_until - now)
            return ProtectionStatus(
                blocked=True,
                until_ts=self._paused_until,
                reason=f"StoplossGuard — {remaining//60}min left",
            )
        return ProtectionStatus(blocked=False)


class CooldownPeriod:
    """
    Per-symbol cooldown after any loss.
    Prevents re-entry on the same symbol immediately after a loser.
    """

    def __init__(self, cooldown_minutes: int = 60):
        self.cooldown_sec = cooldown_minutes * 60
        self._cooldowns: Dict[str, float] = {}

    def record_loss(self, symbol: str) -> None:
        """Call when a trade on `symbol` closes at a loss."""
        self._cooldowns[symbol] = time.time() + self.cooldown_sec
        logger.info(f"[PROTECTION] {symbol}: cooldown {self.cooldown_sec//60}min")

    def check(self, symbol: str) -> ProtectionStatus:
        now = time.time()
        until = self._cooldowns.get(symbol, 0)
        if now < until:
            remaining = int(until - now)
            return ProtectionStatus(
                blocked=True, symbol=symbol, until_ts=until,
                reason=f"cooldown {remaining//60}min",
            )
        # Clean up expired cooldowns
        if symbol in self._cooldowns and now >= self._cooldowns[symbol]:
            del self._cooldowns[symbol]
        return ProtectionStatus(blocked=False)


class MaxDrawdownProtection:
    """
    Pause trading if drawdown over last N trades exceeds threshold.
    Resets after pause period ends.
    """

    def __init__(self, lookback_trades: int = 10, max_drawdown_pct: float = 15.0,
                 pause_hours: int = 6):
        self.lookback = lookback_trades
        self.max_dd = max_drawdown_pct / 100.0
        self.pause_sec = pause_hours * 3600
        self._recent_balances: deque = deque(maxlen=lookback_trades)
        self._paused_until: float = 0

    def record_balance(self, balance: float) -> None:
        """Call after every closed trade with current balance."""
        self._recent_balances.append(balance)
        self._check_trigger()

    def _check_trigger(self) -> None:
        if len(self._recent_balances) < self.lookback:
            return
        peak = max(self._recent_balances)
        current = self._recent_balances[-1]
        if peak > 0:
            dd = (peak - current) / peak
            if dd > self.max_dd:
                self._paused_until = time.time() + self.pause_sec
                logger.warning(
                    f"[PROTECTION] MaxDrawdown triggered — "
                    f"{dd*100:.1f}% over last {self.lookback} trades. "
                    f"Paused {self.pause_sec/3600:.0f}h"
                )

    def check(self) -> ProtectionStatus:
        now = time.time()
        if now < self._paused_until:
            remaining = int(self._paused_until - now)
            return ProtectionStatus(
                blocked=True,
                until_ts=self._paused_until,
                reason=f"MaxDrawdown — {remaining//60}min left",
            )
        return ProtectionStatus(blocked=False)


class LowProfitPairs:
    """
    Disable symbols with negative expectancy over N trades.
    Re-enables after cool-off period to let conditions change.
    """

    def __init__(self, min_trades: int = 5, min_win_rate_pct: float = 30.0,
                 min_expectancy_r: float = 0.0, disable_hours: int = 24):
        self.min_trades = min_trades
        self.min_wr = min_win_rate_pct
        self.min_exp = min_expectancy_r
        self.disable_sec = disable_hours * 3600
        self._disabled: Dict[str, float] = {}
        self._trade_history: Dict[str, List[float]] = {}

    def record_trade(self, symbol: str, pnl_pct: float) -> None:
        """Call after each closed trade."""
        if symbol not in self._trade_history:
            self._trade_history[symbol] = []
        self._trade_history[symbol].append(pnl_pct)

        # Keep rolling window
        if len(self._trade_history[symbol]) > 20:
            self._trade_history[symbol] = self._trade_history[symbol][-20:]

        self._evaluate(symbol)

    def _evaluate(self, symbol: str) -> None:
        trades = self._trade_history.get(symbol, [])
        if len(trades) < self.min_trades:
            return

        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t <= 0]
        wr = len(wins) / len(trades) * 100
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else -1
        expectancy = (wr / 100 * avg_win) + ((1 - wr / 100) * avg_loss)

        if wr < self.min_wr or expectancy < self.min_exp:
            self._disabled[symbol] = time.time() + self.disable_sec
            logger.warning(
                f"[PROTECTION] {symbol} disabled — WR={wr:.0f}% "
                f"expectancy={expectancy:.2f}% for {self.disable_sec/3600:.0f}h"
            )

    def check(self, symbol: str) -> ProtectionStatus:
        now = time.time()
        until = self._disabled.get(symbol, 0)
        if now < until:
            return ProtectionStatus(
                blocked=True, symbol=symbol, until_ts=until,
                reason="low profit pair",
            )
        if symbol in self._disabled and now >= self._disabled[symbol]:
            del self._disabled[symbol]
        return ProtectionStatus(blocked=False)


class ProtectionManager:
    """
    Aggregates all protections. Call check_global() before scanning,
    check_symbol(sym) before each entry, and record_* on trade events.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._init_from_config()

    def _init_from_config(self):
        prot_cfg = self.config.get('protections', {})

        self.enabled_stoploss_guard = prot_cfg.get('stoploss_guard_enabled', True)
        self.enabled_cooldown = prot_cfg.get('cooldown_enabled', True)
        self.enabled_max_dd = prot_cfg.get('max_dd_enabled', True)
        self.enabled_low_profit = prot_cfg.get('low_profit_enabled', True)

        self.stoploss_guard = StoplossGuard(
            lookback_minutes=prot_cfg.get('stoploss_guard_lookback_min', 30),
            trade_limit=prot_cfg.get('stoploss_guard_trade_limit', 3),
            pause_hours=prot_cfg.get('stoploss_guard_pause_hours', 4),
        )
        self.cooldown = CooldownPeriod(
            cooldown_minutes=prot_cfg.get('cooldown_minutes', 60),
        )
        self.max_drawdown = MaxDrawdownProtection(
            lookback_trades=prot_cfg.get('max_dd_lookback_trades', 10),
            max_drawdown_pct=prot_cfg.get('max_dd_pct', 15.0),
            pause_hours=prot_cfg.get('max_dd_pause_hours', 6),
        )
        self.low_profit = LowProfitPairs(
            min_trades=prot_cfg.get('low_profit_min_trades', 5),
            min_win_rate_pct=prot_cfg.get('low_profit_min_wr', 30.0),
            disable_hours=prot_cfg.get('low_profit_disable_hours', 24),
        )

    def reload_config(self, config: dict):
        """Reload settings without losing historical state."""
        self.config = config or {}
        prot_cfg = self.config.get('protections', {})
        self.enabled_stoploss_guard = prot_cfg.get('stoploss_guard_enabled', True)
        self.enabled_cooldown = prot_cfg.get('cooldown_enabled', True)
        self.enabled_max_dd = prot_cfg.get('max_dd_enabled', True)
        self.enabled_low_profit = prot_cfg.get('low_profit_enabled', True)

    def check_global(self) -> ProtectionStatus:
        """Call before entering any new trade. Checks all global protections."""
        if self.enabled_stoploss_guard:
            status = self.stoploss_guard.check()
            if status.blocked:
                return status
        if self.enabled_max_dd:
            status = self.max_drawdown.check()
            if status.blocked:
                return status
        return ProtectionStatus(blocked=False)

    def check_symbol(self, symbol: str) -> ProtectionStatus:
        """Call before entering this specific symbol."""
        if self.enabled_cooldown:
            status = self.cooldown.check(symbol)
            if status.blocked:
                return status
        if self.enabled_low_profit:
            status = self.low_profit.check(symbol)
            if status.blocked:
                return status
        return ProtectionStatus(blocked=False)

    def get_status(self) -> dict:
        """Return current status of all protections for dashboard display."""
        import time
        now = time.time()

        sl_status = self.stoploss_guard.check()
        dd_status = self.max_drawdown.check()

        return {
            'stoploss_guard': {
                'enabled': self.enabled_stoploss_guard,
                'paused': sl_status.blocked,
                'paused_until_sec': max(0, int(self.stoploss_guard._paused_until - now)) if self.stoploss_guard._paused_until > now else 0,
                'recent_stoplosses': len([t for t in self.stoploss_guard._stoploss_times
                                           if t >= now - self.stoploss_guard.lookback_sec]),
                'limit': self.stoploss_guard.trade_limit,
            },
            'cooldown': {
                'enabled': self.enabled_cooldown,
                'active_symbols': len([s for s, t in self.cooldown._cooldowns.items() if t > now]),
                'cooldown_min': self.cooldown.cooldown_sec // 60,
            },
            'max_drawdown': {
                'enabled': self.enabled_max_dd,
                'paused': dd_status.blocked,
                'paused_until_sec': max(0, int(self.max_drawdown._paused_until - now)) if self.max_drawdown._paused_until > now else 0,
                'recent_balances': len(self.max_drawdown._recent_balances),
                'threshold_pct': self.max_drawdown.max_dd * 100,
            },
            'low_profit': {
                'enabled': self.enabled_low_profit,
                'disabled_count': len([s for s, t in self.low_profit._disabled.items() if t > now]),
                'tracked_symbols': len(self.low_profit._trade_history),
            },
        }

    def on_trade_closed(self, symbol: str, pnl: float, pnl_pct: float,
                         exit_reason: str, new_balance: float) -> None:
        """Call after every trade closes. Updates all protection trackers."""
        self.max_drawdown.record_balance(new_balance)
        self.low_profit.record_trade(symbol, pnl_pct)
        if pnl < 0:
            self.cooldown.record_loss(symbol)
            if "STOP" in exit_reason.upper() or "SL" in exit_reason.upper():
                self.stoploss_guard.record_stoploss()
