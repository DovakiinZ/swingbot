"""
Health Monitor for Swingbot — Background health checks + auto recovery.

Runs every 60 seconds in a daemon thread. Checks:
  1. Exchange API connectivity (ping test)
  2. Daily P&L limit (-$15 hard stop)
  3. Consecutive error tracking (auto-reconnect after 5)
  4. Ghost position detection (DB vs broker mismatch)

On failure: logs details + sends Discord alert with action taken.
Sends hourly status report to Discord.
"""
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 60       # seconds between health checks
HOURLY_REPORT = 3600      # seconds between status reports
MAX_RECONNECT_ATTEMPTS = 3


class HealthMonitor:
    """Background health monitoring with Discord alerts and auto-recovery."""

    def __init__(self, config: dict, broker, store, notifier,
                 circuit_breaker, market=None):
        """
        Args:
            config: Full config dict (reloaded each check)
            broker: Active broker instance (paper or live)
            store: SQLiteStore for DB queries
            notifier: Notifier for Discord/Telegram alerts
            circuit_breaker: CircuitBreaker instance
            market: MarketData instance for API ping
        """
        self.config = config
        self.broker = broker
        self.store = store
        self.notifier = notifier
        self.circuit_breaker = circuit_breaker
        self.market = market

        self.consecutive_errors = 0
        self.reconnect_attempts = 0
        self.trading_paused = False
        self.pause_until: Optional[float] = None
        self.start_time = time.time()

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_hourly_report = 0

    def start(self) -> None:
        """Start the health monitor background thread."""
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name='health-monitor'
        )
        self._thread.start()
        logger.warning("[HEALTH] Health monitor started (60s interval)")

    def stop(self) -> None:
        """Signal the monitor to stop."""
        self._stop_event.set()

    @property
    def is_paused(self) -> bool:
        """Check if trading is paused due to health issues."""
        if self.pause_until and time.time() < self.pause_until:
            return True
        if self.pause_until and time.time() >= self.pause_until:
            self.trading_paused = False
            self.pause_until = None
        return self.trading_paused

    def _run_loop(self) -> None:
        """Main loop — runs checks every 60 seconds."""
        # Wait 30s before first check (let bot initialize)
        self._stop_event.wait(30)

        while not self._stop_event.is_set():
            try:
                self._check_all()
            except Exception as e:
                logger.error(f"[HEALTH] Check loop error: {e}")
                self.consecutive_errors += 1
            self._stop_event.wait(CHECK_INTERVAL)

    def _check_all(self) -> None:
        """Run all health checks and send alerts on failures."""
        issues = []

        # 1. API connectivity
        api_ok = self._check_api()
        if not api_ok:
            issues.append("Exchange API unreachable")
            self._handle_api_failure()

        # 2. Daily P&L limit
        pnl_ok, pnl_val = self._check_daily_pnl()
        if not pnl_ok:
            issues.append(f"Daily loss limit breached (${pnl_val:.2f})")
            self._pause_trading(hours=24, reason="Daily P&L limit exceeded")

        # 3. Consecutive errors
        if self.consecutive_errors >= self.config.get('api_failure_limit', 5):
            issues.append(f"Consecutive errors: {self.consecutive_errors}")

        # 4. Ghost positions
        ghosts = self._check_ghost_positions()
        if ghosts:
            issues.append(f"Ghost positions: {', '.join(ghosts)}")

        # Alert on issues
        if issues:
            alert_text = (
                f"[HEALTH ALERT]\n"
                + "\n".join(f"- {issue}" for issue in issues)
                + f"\n\nAction: {'Trading PAUSED' if self.is_paused else 'Monitoring'}"
            )
            logger.warning(alert_text)
            try:
                self.notifier.notify_text(alert_text, channel="warnings")
            except Exception:
                pass
        else:
            # Reset error counter on successful check
            self.consecutive_errors = 0
            self.reconnect_attempts = 0

        # Hourly status report
        now = time.time()
        if now - self._last_hourly_report >= HOURLY_REPORT:
            self._send_hourly_report()
            self._last_hourly_report = now

    def _check_api(self) -> bool:
        """Verify exchange API is reachable."""
        try:
            bal = self.broker.get_balance()
            return bal is not None and bal >= 0
        except Exception as e:
            logger.error(f"[HEALTH] API check failed: {e}")
            return False

    def _handle_api_failure(self) -> None:
        """Auto-reconnect on API failure, up to MAX_RECONNECT_ATTEMPTS."""
        self.consecutive_errors += 1
        self.reconnect_attempts += 1

        if self.reconnect_attempts <= MAX_RECONNECT_ATTEMPTS:
            logger.warning(
                f"[HEALTH] API reconnect attempt {self.reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS}"
            )
            try:
                # Try to re-initialize the exchange connection
                if self.market and hasattr(self.market, 'exchange'):
                    self.market.exchange.load_markets()
                    logger.warning("[HEALTH] API reconnected successfully")
                    self.consecutive_errors = 0
            except Exception as e:
                logger.error(f"[HEALTH] Reconnect failed: {e}")
        else:
            alert = (
                f"[HEALTH] API reconnect failed after {MAX_RECONNECT_ATTEMPTS} attempts. "
                f"Manual intervention may be needed."
            )
            logger.error(alert)
            try:
                self.notifier.notify_text(alert, channel="warnings")
            except Exception:
                pass

    def _check_daily_pnl(self) -> tuple:
        """Check if daily P&L exceeds the hard dollar loss limit."""
        try:
            limit = self.config.get('max_daily_loss_usd', 15.0)
            today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            stats = self.store.get_daily_stats(today)
            pnl = stats.get('pnl', 0.0) if isinstance(stats, dict) else 0.0
            if pnl <= -limit:
                return False, pnl
            return True, pnl
        except Exception as e:
            logger.error(f"[HEALTH] PnL check error: {e}")
            return True, 0.0  # Don't block on check failure

    def _check_ghost_positions(self) -> list:
        """Detect positions in DB that don't exist on the broker."""
        try:
            db_positions = self.store.get_open_positions()
            broker_positions = self.broker.get_open_positions()
            broker_symbols = {p.symbol for p in broker_positions}
            db_symbols = {p.symbol for p in db_positions}
            ghosts = db_symbols - broker_symbols
            return list(ghosts) if ghosts else []
        except Exception:
            return []  # Don't alert on check failures

    def _pause_trading(self, hours: int = 24, reason: str = "") -> None:
        """Pause all trading for N hours."""
        self.trading_paused = True
        self.pause_until = time.time() + (hours * 3600)
        msg = f"[HEALTH] Trading PAUSED for {hours}h. Reason: {reason}"
        logger.warning(msg)
        try:
            self.notifier.notify_text(msg, channel="warnings")
        except Exception:
            pass

    def _send_hourly_report(self) -> None:
        """Send a routine status report to Discord."""
        try:
            bal = self.broker.get_balance()
            positions = self.broker.get_open_positions()
            today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            stats = self.store.get_daily_stats(today)
            pnl = stats.get('pnl', 0.0) if isinstance(stats, dict) else 0.0

            uptime_sec = time.time() - self.start_time
            uptime_h = uptime_sec / 3600

            text = (
                f"[HOURLY STATUS]\n"
                f"Balance: ${bal:.2f}\n"
                f"Open positions: {len(positions)}\n"
                f"Day PnL: ${pnl:+.2f}\n"
                f"Bot uptime: {uptime_h:.1f}h\n"
                f"API errors: {self.circuit_breaker.api_errors}\n"
                f"Health: {'PAUSED' if self.is_paused else 'OK'}"
            )
            self.notifier.notify_text(text, channel="general")
            logger.info(f"[HEALTH] Hourly report sent")
        except Exception as e:
            logger.error(f"[HEALTH] Hourly report failed: {e}")
