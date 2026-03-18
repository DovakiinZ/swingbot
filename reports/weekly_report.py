"""
Weekly trading report for Swingbot.
Auto-generates and sends a comprehensive weekly report every Friday at 20:00 UTC.
Reports are saved as JSON and TXT files.
"""
import json
import logging
import math
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class WeeklyStats:
    week: str                  # "2024-W11"
    period: str                # "11 Mar - 17 Mar 2024"
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    best_trade: dict           # {symbol, pnl, pnl_pct}
    worst_trade: dict
    avg_win: float
    avg_loss: float
    expectancy: float          # (win_rate * avg_win) - (loss_rate * avg_loss)
    sharpe_ratio: float
    balance_start: float
    balance_end: float
    growth_pct: float
    compounding_phase: int
    phase_progress_pct: float
    top_symbol: str
    worst_symbol: str
    avg_hold_hours: float


class WeeklyReport:
    """
    Generates and sends weekly trading reports.
    Called from run.py each cycle — only sends on Friday 20:00-20:10 UTC.
    Tracks last sent week in SQLite to avoid duplicates.
    """

    def __init__(self, store, config: dict = None):
        self.store = store
        self.config = config or {}
        self._ensure_table()
        self.report_dir = Path('reports/weekly')
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_table(self) -> None:
        """Create tracking table for sent reports."""
        conn = self.store.get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_reports_sent (
                week TEXT PRIMARY KEY,
                sent_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _was_sent(self, week: str) -> bool:
        """Check if a report for this week was already sent."""
        conn = self.store.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT week FROM weekly_reports_sent WHERE week = ?", (week,))
        row = cursor.fetchone()
        conn.close()
        return row is not None

    def _mark_sent(self, week: str) -> None:
        """Mark a week's report as sent."""
        conn = self.store.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO weekly_reports_sent (week, sent_at) VALUES (?, ?)",
            (week, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()

    def check_and_send(self, notifier) -> None:
        """
        Called from run.py every cycle.
        Only sends if:
        1. Today is Friday (or configured day)
        2. Hour is 20:00-20:10 UTC (or configured hour)
        3. Report hasn't been sent for this week yet
        """
        notif_config = self.config.get('notifications', {})
        report_day = notif_config.get('weekly_report_day', 'friday').lower()
        report_hour = notif_config.get('weekly_report_hour_utc', 20)

        now = datetime.now(timezone.utc)
        day_names = ['monday', 'tuesday', 'wednesday', 'thursday',
                     'friday', 'saturday', 'sunday']

        current_day = day_names[now.weekday()]
        if current_day != report_day:
            return

        if now.hour != report_hour:
            return

        # Only send in first 10 minutes of the hour
        if now.minute > 10:
            return

        week_str = now.strftime('%G-W%V')
        if self._was_sent(week_str):
            return

        try:
            stats = self.generate_stats(now)
            if stats is None:
                logger.info("[WEEKLY] No trades this week — skipping report")
                return

            # Save to files
            self._save_report(stats)

            # Send notification
            if notifier:
                notifier.notify_weekly_report(asdict(stats))

            self._mark_sent(week_str)
            logger.warning(f"[WEEKLY] Report sent for {week_str}")

        except Exception as e:
            logger.error(f"[WEEKLY] Report generation failed: {e}", exc_info=True)

    def generate_stats(self, ref_date: Optional[datetime] = None) -> Optional[WeeklyStats]:
        """
        Generate weekly stats for the week containing ref_date.
        Returns None if no trades found.
        """
        if ref_date is None:
            ref_date = datetime.now(timezone.utc)

        # Calculate week boundaries (Monday 00:00 to Sunday 23:59)
        weekday = ref_date.weekday()
        monday = ref_date - timedelta(days=weekday)
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

        start_ts = int(monday.timestamp() * 1000)
        end_ts = int(sunday.timestamp() * 1000)

        # Fetch closed trades for this week
        conn = self.store.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM positions
            WHERE status = 'CLOSED'
            AND exit_time >= ? AND exit_time < ?
            ORDER BY exit_time
        """, (start_ts, end_ts))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return None

        trades = [dict(r) for r in rows]
        count = len(trades)
        wins = sum(1 for t in trades if (t.get('pnl') or 0) > 0)
        losses = count - wins
        win_rate = (wins / count * 100) if count > 0 else 0

        pnls = [t.get('pnl', 0) or 0 for t in trades]
        total_pnl = sum(pnls)

        win_pnls = [p for p in pnls if p > 0]
        loss_pnls = [p for p in pnls if p < 0]
        avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0

        # Expectancy
        wr = win_rate / 100
        expectancy = (wr * avg_win) - ((1 - wr) * abs(avg_loss))

        # Best / worst trade
        best_t = max(trades, key=lambda t: t.get('pnl', 0) or 0)
        worst_t = min(trades, key=lambda t: t.get('pnl', 0) or 0)

        best_trade = {
            'symbol': best_t.get('symbol', ''),
            'pnl': best_t.get('pnl', 0),
            'pnl_pct': best_t.get('pnl_percent', 0),
        }
        worst_trade = {
            'symbol': worst_t.get('symbol', ''),
            'pnl': worst_t.get('pnl', 0),
            'pnl_pct': worst_t.get('pnl_percent', 0),
        }

        # Symbol performance
        sym_pnl: dict = {}
        for t in trades:
            sym = t.get('symbol', 'Unknown')
            sym_pnl[sym] = sym_pnl.get(sym, 0) + (t.get('pnl', 0) or 0)

        top_symbol = max(sym_pnl, key=sym_pnl.get) if sym_pnl else 'N/A'
        worst_symbol = min(sym_pnl, key=sym_pnl.get) if sym_pnl else 'N/A'

        # Average hold time
        hold_hours_list = []
        for t in trades:
            entry_t = t.get('entry_time', 0) or 0
            exit_t = t.get('exit_time', 0) or 0
            if entry_t and exit_t:
                hold_hours_list.append((exit_t - entry_t) / 3600000)
        avg_hold_hours = sum(hold_hours_list) / len(hold_hours_list) if hold_hours_list else 0

        # Sharpe ratio
        sharpe = 0.0
        if len(pnls) >= 3:
            log_returns = []
            for p in pnls:
                pct = (p / 100) if abs(p) < 1000 else p  # rough normalization
                try:
                    log_returns.append(math.log(1 + p / max(abs(total_pnl), 1)))
                except (ValueError, ZeroDivisionError):
                    pass
            if len(log_returns) >= 3:
                import numpy as np
                mean_r = float(np.mean(log_returns))
                std_r = float(np.std(log_returns))
                if std_r > 0:
                    sharpe = mean_r / std_r

        # Balance tracking
        balance_end = self.store.get_peak_balance() or 0
        base_balance = self.config.get('base_balance', 100.0)
        balance_start = balance_end - total_pnl

        # Growth
        growth_pct = ((balance_end - base_balance) / base_balance * 100) if base_balance > 0 else 0

        # Compounding phase
        if balance_end >= base_balance * 5.0:
            phase = 3
        elif balance_end >= base_balance * 2.5:
            phase = 2
        else:
            phase = 1

        # Phase progress
        phase_targets = {1: base_balance * 2.5, 2: base_balance * 5.0, 3: base_balance * 10.0}
        phase_starts = {1: base_balance, 2: base_balance * 2.5, 3: base_balance * 5.0}
        target = phase_targets.get(phase, base_balance * 10)
        start = phase_starts.get(phase, base_balance)
        phase_progress = ((balance_end - start) / (target - start) * 100) if target > start else 0
        phase_progress = max(0, min(100, phase_progress))

        week_str = ref_date.strftime('%G-W%V')
        period = f"{monday.strftime('%d %b')} \u2013 {sunday.strftime('%d %b %Y')}"

        return WeeklyStats(
            week=week_str,
            period=period,
            trades=count,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            total_pnl=total_pnl,
            best_trade=best_trade,
            worst_trade=worst_trade,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy,
            sharpe_ratio=sharpe,
            balance_start=balance_start,
            balance_end=balance_end,
            growth_pct=growth_pct,
            compounding_phase=phase,
            phase_progress_pct=phase_progress,
            top_symbol=top_symbol,
            worst_symbol=worst_symbol,
            avg_hold_hours=avg_hold_hours,
        )

    def _save_report(self, stats: WeeklyStats) -> None:
        """Save report as JSON and TXT files."""
        week_safe = stats.week.replace('-', '_')

        # JSON
        json_path = self.report_dir / f"week_{week_safe}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(asdict(stats), f, indent=2, ensure_ascii=False)

        # TXT
        txt_path = self.report_dir / f"week_{week_safe}.txt"
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(f"Swingbot Weekly Report — {stats.week}\n")
            f.write(f"Period: {stats.period}\n")
            f.write("=" * 50 + "\n")
            f.write(f"Trades:      {stats.trades}\n")
            f.write(f"Wins:        {stats.wins}\n")
            f.write(f"Losses:      {stats.losses}\n")
            f.write(f"Win Rate:    {stats.win_rate:.1f}%\n")
            f.write(f"Total P&L:   ${stats.total_pnl:+.2f}\n")
            f.write(f"Expectancy:  ${stats.expectancy:.2f} / trade\n")
            f.write(f"Sharpe:      {stats.sharpe_ratio:.2f}\n")
            f.write(f"Avg Hold:    {stats.avg_hold_hours:.1f} hours\n")
            f.write(f"Best Trade:  {stats.best_trade}\n")
            f.write(f"Worst Trade: {stats.worst_trade}\n")
            f.write(f"Top Symbol:  {stats.top_symbol}\n")
            f.write(f"Worst Symbol:{stats.worst_symbol}\n")
            f.write("=" * 50 + "\n")
            f.write(f"Balance: ${stats.balance_start:.2f} → ${stats.balance_end:.2f}\n")
            f.write(f"Growth:  {stats.growth_pct:+.1f}%\n")
            f.write(f"Phase:   {stats.compounding_phase} ({stats.phase_progress_pct:.0f}% complete)\n")

        logger.info(f"[WEEKLY] Report saved: {json_path}")

    def get_latest_report(self) -> Optional[dict]:
        """Get the most recent weekly report as a dict (for dashboard)."""
        json_files = sorted(self.report_dir.glob('week_*.json'), reverse=True)
        if not json_files:
            return None
        try:
            with open(json_files[0], 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def get_recent_reports(self, limit: int = 4) -> list:
        """Get recent weekly reports (for dashboard)."""
        json_files = sorted(self.report_dir.glob('week_*.json'), reverse=True)[:limit]
        reports = []
        for fp in json_files:
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    reports.append(json.load(f))
            except Exception:
                continue
        return reports

    def force_send(self, notifier) -> Optional[dict]:
        """Force send the current week's report immediately (from dashboard button)."""
        try:
            stats = self.generate_stats()
            if stats is None:
                return None
            self._save_report(stats)
            if notifier:
                from dataclasses import asdict
                notifier.notify_weekly_report(asdict(stats))
            return asdict(stats)
        except Exception as e:
            logger.error(f"[WEEKLY] Force send failed: {e}", exc_info=True)
            return None
