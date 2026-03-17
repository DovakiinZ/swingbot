import json
import math
import os
from datetime import datetime
from typing import Dict, Any, List
from storage.sqlite_store import SQLiteStore
from core.utils import save_json

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


class DailyReport:
    def __init__(self, store: SQLiteStore, report_dir: str = "reports/out"):
        self.store = store
        self.report_dir = report_dir
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)

    def generate(self, date_str: str = None) -> Dict[str, Any]:
        """Generate daily report for the given date (YYYY-MM-DD)."""
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        conn = self.store.get_connection()
        cursor = conn.cursor()

        start_ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
        end_ts = start_ts + 86400000

        cursor.execute("""
            SELECT * FROM positions
            WHERE status = 'CLOSED' AND exit_time >= ? AND exit_time < ?
        """, (start_ts, end_ts))
        closed_positions = cursor.fetchall()

        daily_pnl = sum([p['pnl'] for p in closed_positions])
        wins = len([p for p in closed_positions if p['pnl'] > 0])
        losses = len([p for p in closed_positions if p['pnl'] <= 0])
        total_closed = len(closed_positions)
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

        sharpe = self.calculate_sharpe_ratio(date_str)

        report = {
            "date": date_str,
            "pnl_usdt": daily_pnl,
            "trades_count": total_closed,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "sharpe_ratio": sharpe,
            "positions_closed": [dict(p) for p in closed_positions]
        }

        filepath = os.path.join(self.report_dir, f"report_{date_str}.json")
        save_json(filepath, report)

        conn.close()
        return report

    def generate_report(self, date_str: str = None) -> Dict[str, Any]:
        """Alias for generate() for backward compatibility."""
        return self.generate(date_str)

    def calculate_sharpe_ratio(self, date_str: str, risk_free_rate: float = 0.0) -> float:
        """
        Sharpe Ratio = (Mean log return - Risk free rate) / Std of log returns
        Uses log returns (correct for compounding).

        SR < 1.0  = poor
        SR 1-2    = good
        SR > 2.0  = excellent
        """
        trades = self.store.get_closed_trades_for_date(date_str)
        if len(trades) < 3:
            return 0.0

        log_returns = []
        for t in trades:
            pnl_pct = t.get('pnl_percent')
            if pnl_pct and pnl_pct != 0:
                try:
                    p0 = 100.0
                    p1 = 100.0 * (1 + pnl_pct / 100)
                    if p1 > 0:
                        log_returns.append(math.log(p1 / p0))
                except (ValueError, ZeroDivisionError):
                    pass

        if len(log_returns) < 3:
            return 0.0

        if not HAS_NUMPY:
            mean_r = sum(log_returns) / len(log_returns)
            variance = sum((r - mean_r) ** 2 for r in log_returns) / len(log_returns)
            std_r = variance ** 0.5
        else:
            mean_r = np.mean(log_returns)
            std_r = np.std(log_returns)

        if std_r == 0:
            return 0.0

        return float((mean_r - risk_free_rate) / std_r)

    def print_summary(self, report: Dict[str, Any]):
        print("\n" + "=" * 40)
        print(f" DAILY REPORT: {report['date']}")
        print("=" * 40)
        print(f"PnL: {report['pnl_usdt']:.2f} USDT")
        print(f"Trades: {report['trades_count']} (W: {report['wins']} / L: {report['losses']})")
        print(f"Win Rate: {report['win_rate']:.1f}%")
        print(f"Sharpe Ratio: {report.get('sharpe_ratio', 0):.2f}")
        print("=" * 40 + "\n")
